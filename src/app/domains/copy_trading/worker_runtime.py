import asyncio
import json
import logging
import signal
import socket
import time
import uuid
import base64
import io
from datetime import datetime, timedelta, timezone

import redis
from pydantic import BaseModel
from sqlalchemy import delete, select

import app.models  # noqa: F401 - register string-based ORM relationships for standalone workers
from app.core.config import settings
from app.core.database import SessionLocal
from app.domains.copy_trading.models import (
    AutomationConfidence,
    ChannelMessageSample,
    ChannelProfile,
    CopyActivityEvent,
    CopyActivityLevel,
    TelegramConnection,
    TelegramConnectionState,
    TelegramSource,
    TelegramSourceState,
    TelegramSourceType,
)
from app.domains.copy_trading.security import SessionCipher
from app.domains.copy_trading.streams import CopyEvent, RedisStreamBus, StreamName


logger = logging.getLogger("copy-trading.worker")


class StreamWorker:
    def __init__(self, *, stream: StreamName, group: str, handler):
        self.client = redis.Redis.from_url(settings.COPY_TRADING_REDIS_URL, decode_responses=True)
        self.bus = RedisStreamBus(self.client)
        self.stream = stream
        self.group = group
        self.consumer = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        self.handler = handler
        self.running = True
        self.last_maintenance_at = 0.0

    def run(self) -> None:
        self.bus.ensure_group(self.stream, self.group)
        if self.group == "copy-execution":
            from app.domains.copy_trading.workers import publish_unresolved_intents
            publish_unresolved_intents(self.client)
        elif self.group == "copy-learning":
            purge_expired_samples()
            recover_learning_sources(self.client)
        self.client.setex(f"copy:heartbeat:{self.group}:{self.consumer}", 30, datetime.now(timezone.utc).isoformat())
        while self.running:
            self._run_maintenance()
            self._claim_stale_messages()
            rows = self.client.xreadgroup(self.group, self.consumer, {self.stream.value: ">"}, count=10, block=settings.COPY_TRADING_CONSUMER_BLOCK_MS)
            self.client.setex(f"copy:heartbeat:{self.group}:{self.consumer}", 30, datetime.now(timezone.utc).isoformat())
            for _, messages in rows:
                for message_id, fields in messages:
                    self._process_message(message_id, fields)

    def _run_maintenance(self) -> None:
        if self.group != "copy-signal":
            return
        now = time.monotonic()
        if now - self.last_maintenance_at < 5:
            return
        from app.domains.copy_trading.workers import expire_signal_threads

        expired_count = expire_signal_threads()
        if expired_count:
            logger.info("Expired incomplete signal threads count=%s", expired_count)
        self.last_maintenance_at = now

    def _process_message(self, message_id: str, fields: dict) -> None:
        event = CopyEvent.from_fields(fields)
        dedupe_key = f"copy:processed:{self.group}:{event.idempotency_key}"
        try:
            if self.client.get(dedupe_key):
                return
            self.handler(event, self.client)
            self.client.setex(dedupe_key, 604800, event.event_id)
        except Exception as exc:
            logger.exception(
                "Copy-trading event failed stream=%s group=%s event_type=%s correlation_id=%s",
                self.stream.value,
                self.group,
                event.event_type,
                event.correlation_id,
            )
            self.client.xadd(
                StreamName.dead_letters.value,
                {
                    **event.to_fields(),
                    "source_stream": self.stream.value,
                    "consumer_group": self.group,
                    "source_message_id": message_id,
                    "error": str(exc),
                },
            )
        finally:
            self.client.xack(self.stream.value, self.group, message_id)

    def _claim_stale_messages(self) -> None:
        try:
            claimed = self.client.xautoclaim(
                self.stream.value,
                self.group,
                self.consumer,
                min_idle_time=300_000,
                start_id="0-0",
                count=10,
            )
        except Exception as exc:
            if "unknown command" in str(exc).lower():
                return
            raise
        messages = claimed[1] if len(claimed) > 1 else []
        for message_id, fields in messages:
            self._process_message(message_id, fields)


class TelegramSessionRuntime:
    def __init__(self):
        self.redis = redis.Redis.from_url(settings.COPY_TRADING_REDIS_URL, decode_responses=True)
        self.bus = RedisStreamBus(self.redis)
        self.clients = {}
        self.qr_logins = {}
        self.cipher = SessionCipher(settings.ENCRYPTION_KEY)

    def _auth_update(self, auth_id: str, **values) -> None:
        key = f"copy:telegram:auth:{auth_id}"
        self.redis.hset(key, mapping={name: str(value) for name, value in values.items() if value is not None})
        self.redis.expire(key, 600)

    async def _finalize(self, auth_id: str, client) -> None:
        me = await client.get_me()
        from telethon.sessions import StringSession

        session_value = StringSession.save(client.session)
        with SessionLocal() as db:
            connection = db.get(TelegramConnection, uuid.UUID(auth_id))
            if connection is None:
                await client.disconnect()
                return
            connection.telegram_user_id = int(me.id)
            connection.display_name = " ".join(part for part in (getattr(me, "first_name", None), getattr(me, "last_name", None)) if part) or None
            connection.username = getattr(me, "username", None)
            connection.encrypted_session = self.cipher.encrypt(session_value)
            connection.state = TelegramConnectionState.ready
            connection.reauthentication_reason = None
            connection.last_heartbeat_at = datetime.now(timezone.utc)
            db.commit()
        await self._cache_dialogs(auth_id, client)
        self._auth_update(auth_id, state="ready", message="Telegram connected")
        await self._attach_updates(auth_id, client)

    async def _cache_dialogs(self, connection_id: str, client) -> list[dict]:
        dialogs = []
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            is_channel = bool(getattr(entity, "broadcast", False))
            is_group = bool(getattr(entity, "megagroup", False) or getattr(dialog, "is_group", False))
            if not (is_channel or is_group):
                continue
            dialogs.append({"chat_id": int(dialog.id), "title": dialog.name or "Untitled", "username": getattr(entity, "username", None), "source_type": "channel" if is_channel else "group", "is_admin": bool(getattr(entity, "admin_rights", None) or getattr(entity, "creator", False))})
        self.redis.setex(f"copy:telegram:dialogs:{connection_id}", 86400, json.dumps(dialogs))
        return dialogs

    async def _attach_updates(self, connection_id: str, client) -> None:
        from telethon import events

        async def publish_message(event_type: str, event) -> None:
            chat_id = int(event.chat_id)
            with SessionLocal() as db:
                source = db.execute(select(TelegramSource).where(TelegramSource.connection_id == uuid.UUID(connection_id), TelegramSource.telegram_chat_id == chat_id)).scalar_one_or_none()
                connection = db.get(TelegramConnection, uuid.UUID(connection_id))
                if source is None or connection is None or connection.is_paused or source.is_paused or source.state not in {TelegramSourceState.ready, TelegramSourceState.active}:
                    return
                source_id = str(source.id)
                source_type = source.source_type
            message = event.message
            text = message.message or ""
            if not text.strip():
                return
            sender_is_admin = source_type == TelegramSourceType.channel
            if (
                source_type == TelegramSourceType.group
                and message.sender_id is not None
            ):
                permission_key = (
                    f"copy:telegram:admin:{connection_id}:{chat_id}:"
                    f"{int(message.sender_id)}"
                )
                cached_permission = self.redis.get(permission_key)
                if cached_permission is None:
                    try:
                        permissions = await client.get_permissions(
                            chat_id,
                            int(message.sender_id),
                        )
                        sender_is_admin = bool(
                            getattr(permissions, "is_admin", False)
                            or getattr(permissions, "is_creator", False)
                        )
                    except Exception:
                        sender_is_admin = False
                    self.redis.setex(
                        permission_key,
                        300,
                        "1" if sender_is_admin else "0",
                    )
                else:
                    sender_is_admin = cached_permission == "1"
            payload = {
                "connection_id": connection_id,
                "source_id": source_id,
                "chat_id": chat_id,
                "message_id": int(message.id),
                "reply_to_message_id": getattr(getattr(message, "reply_to", None), "reply_to_msg_id", None),
                "sender_id": int(message.sender_id) if message.sender_id else None,
                "sender_is_admin": sender_is_admin,
                "text": text,
                "is_forward": bool(message.fwd_from),
                "occurred_at": message.date.astimezone(timezone.utc).isoformat(),
            }
            self.bus.publish(CopyEvent.new(stream=StreamName.telegram_messages, event_type=event_type, correlation_id=str(uuid.uuid4()), payload=payload, idempotency_key=f"{connection_id}:{chat_id}:{message.id}:{event_type}"))

        client.add_event_handler(lambda event: publish_message("message.created", event), events.NewMessage())
        client.add_event_handler(lambda event: publish_message("message.edited", event), events.MessageEdited())
        async def publish_deleted(event) -> None:
            chat_id = int(event.chat_id) if event.chat_id else None
            if chat_id is None:
                return
            with SessionLocal() as db:
                source = db.execute(select(TelegramSource).where(TelegramSource.connection_id == uuid.UUID(connection_id), TelegramSource.telegram_chat_id == chat_id)).scalar_one_or_none()
                if source is None:
                    return
                source_id = str(source.id)
            for message_id in event.deleted_ids:
                self.bus.publish(CopyEvent.new(stream=StreamName.telegram_messages, event_type="message.deleted", correlation_id=str(uuid.uuid4()), payload={"connection_id": connection_id, "source_id": source_id, "chat_id": chat_id, "message_id": int(message_id)}, idempotency_key=f"{connection_id}:{chat_id}:{message_id}:deleted"))
        client.add_event_handler(publish_deleted, events.MessageDeleted())
        self.clients[f"connection:{connection_id}"] = client

    async def _new_client(self):
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        return TelegramClient(StringSession(), settings.TELEGRAM_API_ID, settings.TELEGRAM_API_HASH)

    async def handle(self, event: CopyEvent) -> None:
        from telethon.errors import SessionPasswordNeededError

        auth_id = event.payload.get("auth_id")
        if event.event_type == "auth.phone.start":
            client = await self._new_client()
            await client.connect()
            sent = await client.send_code_request(event.payload["phone"])
            self.clients[auth_id] = (client, event.payload["phone"], sent.phone_code_hash)
            self._auth_update(auth_id, state="code_required", message="Enter the code Telegram sent")
        elif event.event_type == "auth.phone.code":
            client, phone, code_hash = self.clients[auth_id]
            try:
                await client.sign_in(phone=phone, code=event.payload["code"], phone_code_hash=code_hash)
            except SessionPasswordNeededError:
                self._auth_update(auth_id, state="password_required", message="Enter your Telegram two-step password")
                return
            await self._finalize(auth_id, client)
        elif event.event_type == "auth.phone.password":
            client, _, _ = self.clients[auth_id]
            await client.sign_in(password=event.payload["password"])
            event.payload["password"] = ""
            await self._finalize(auth_id, client)
        elif event.event_type == "auth.qr.start":
            client = await self._new_client()
            await client.connect()
            qr_login = await client.qr_login()
            self.clients[auth_id] = (client, None, None)
            self.qr_logins[auth_id] = qr_login
            import qrcode
            image = qrcode.make(qr_login.url)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            qr_data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
            self._auth_update(auth_id, state="qr_required", qr_url=qr_data_url, message="Scan this QR code in Telegram")
            asyncio.create_task(self._wait_qr(auth_id, client, qr_login))
        elif event.event_type == "dialogs.refresh":
            connection_id = event.payload["connection_id"]
            request_id = event.payload["request_id"]
            response_key = f"copy:telegram:dialogs-response:{request_id}"
            client = self.clients.get(f"connection:{connection_id}")
            if client is None:
                self.redis.setex(
                    response_key,
                    30,
                    json.dumps(
                        {
                            "error": (
                                "The Telegram session is reconnecting. "
                                "Try refreshing again in a moment."
                            )
                        }
                    ),
                )
                return
            try:
                dialogs = await self._cache_dialogs(connection_id, client)
                self.redis.setex(
                    response_key,
                    30,
                    json.dumps(dialogs),
                )
            except Exception:
                logger.exception(
                    "Could not refresh Telegram dialogs connection_id=%s",
                    connection_id,
                )
                self.redis.setex(
                    response_key,
                    30,
                    json.dumps(
                        {
                            "error": (
                                "Telegram could not refresh channels and groups. "
                                "Try again in a moment."
                            )
                        }
                    ),
                )
        elif event.event_type == "connection.disconnect":
            client = self.clients.pop(f"connection:{event.payload['connection_id']}", None)
            if client:
                await client.disconnect()

    async def _wait_qr(self, auth_id: str, client, qr_login) -> None:
        from telethon.errors import SessionPasswordNeededError
        try:
            await qr_login.wait(timeout=120)
            await self._finalize(auth_id, client)
        except SessionPasswordNeededError:
            self._auth_update(auth_id, state="password_required", message="Enter your Telegram two-step password")
        except Exception as exc:
            self._auth_update(auth_id, state="failed", message=f"Telegram sign-in failed: {exc}")

    async def restore(self) -> None:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        with SessionLocal() as db:
            connections = list(db.execute(select(TelegramConnection).where(TelegramConnection.state == TelegramConnectionState.ready)).scalars())
        for connection in connections:
            try:
                client = TelegramClient(StringSession(self.cipher.decrypt(connection.encrypted_session)), settings.TELEGRAM_API_ID, settings.TELEGRAM_API_HASH)
                await client.connect()
                if not await client.is_user_authorized():
                    raise RuntimeError("Telegram authorization expired")
                await self._cache_dialogs(str(connection.id), client)
                await self._attach_updates(str(connection.id), client)
            except Exception as exc:
                with SessionLocal() as db:
                    item = db.get(TelegramConnection, connection.id)
                    item.state = TelegramConnectionState.reauthentication_required
                    item.reauthentication_reason = str(exc)
                    db.commit()

    async def run(self) -> None:
        await self.restore()
        group = "telegram-session"
        consumer = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        self.bus.ensure_group(StreamName.telegram_commands, group)
        while True:
            rows = await asyncio.to_thread(self.redis.xreadgroup, group, consumer, {StreamName.telegram_commands.value: ">"}, count=10, block=1000)
            self.redis.setex(f"copy:heartbeat:{group}:{consumer}", 30, datetime.now(timezone.utc).isoformat())
            for _, messages in rows:
                for message_id, fields in messages:
                    event = CopyEvent.from_fields(fields)
                    try:
                        await self.handle(event)
                    except Exception as exc:
                        logger.exception("Telegram command failed", extra={"correlation_id": event.correlation_id})
                        if event.payload.get("auth_id"):
                            self._auth_update(event.payload["auth_id"], state="failed", message=f"Telegram sign-in failed: {exc}")
                    finally:
                        self.redis.xack(StreamName.telegram_commands.value, group, message_id)
                        self.redis.xdel(StreamName.telegram_commands.value, message_id)


def learning_handler(event: CopyEvent, client) -> None:
    if event.event_type != "source.learn":
        return
    source_id = uuid.UUID(event.payload["source_id"])
    lock = client.lock(
        f"copy:learning-lock:{source_id}",
        timeout=240,
        blocking_timeout=1,
    )
    if not lock.acquire(blocking=True):
        return
    try:
        asyncio.run(_learn_source_with_timeout(source_id))
    except Exception as exc:
        logger.exception(
            "Channel learning failed source_id=%s correlation_id=%s",
            source_id,
            event.correlation_id,
        )
        _mark_learning_failed(
            source_id,
            "Channel analysis failed. Try analyzing the channel again.",
            str(exc),
        )
    finally:
        lock.release()


async def _learn_source_with_timeout(
    source_id: uuid.UUID, timeout_seconds: float = 180
) -> None:
    await asyncio.wait_for(_learn_source(source_id), timeout=timeout_seconds)


def _mark_learning_failed(source_id: uuid.UUID, user_message: str, internal_error: str) -> None:
    with SessionLocal() as db:
        source = db.get(TelegramSource, source_id)
        if source is None:
            return
        source.state = TelegramSourceState.unsupported
        source.unsupported_reason = user_message
        db.add(
            CopyActivityEvent(
                user_id=source.user_id,
                source_id=source.id,
                correlation_id=str(uuid.uuid4()),
                action="source.learning_failed",
                level=CopyActivityLevel.error,
                title="Channel analysis failed",
                body=user_message,
                parsed_details={"reason": "learning_failed"},
                broker_details={},
            )
        )
        db.commit()


def recover_learning_sources(client) -> None:
    bus = RedisStreamBus(client)
    recovered_at = int(datetime.now(timezone.utc).timestamp())
    with SessionLocal() as db:
        sources = list(
            db.execute(
                select(TelegramSource).where(
                    TelegramSource.state == TelegramSourceState.learning
                )
            ).scalars()
        )
    for source in sources:
        bus.publish(
            CopyEvent.new(
                stream=StreamName.learning_jobs,
                event_type="source.learn",
                correlation_id=str(uuid.uuid4()),
                payload={"source_id": str(source.id)},
                idempotency_key=f"recover-learn:{source.id}:{recovered_at}",
            )
        )
    if sources:
        logger.info("Republished %s stranded channel learning jobs", len(sources))


def purge_expired_samples() -> None:
    with SessionLocal() as db:
        db.execute(
            delete(ChannelMessageSample).where(
                ChannelMessageSample.expires_at <= datetime.now(timezone.utc)
            )
        )
        db.commit()


def _build_learning_prompt(samples: list[dict], max_chars: int = 60_000) -> str:
    compact = []
    for sample in samples[:60]:
        item = dict(sample)
        item["text"] = str(item.get("text", ""))[:900]
        candidate = json.dumps([*compact, item], ensure_ascii=True)
        if len(candidate) > max_chars:
            break
        compact.append(item)
    return json.dumps(compact, ensure_ascii=True)


class LearningResult(BaseModel):
    signal_style: str
    recommended_assembly_window_seconds: int
    confidence: AutomationConfidence
    confidence_score: float
    image_primary: bool
    supported_actions: list[str]
    author_pattern: dict


def _learning_source_outcome(
    *,
    confidence: AutomationConfidence,
    image_primary: bool,
) -> tuple[TelegramSourceState, str | None]:
    if image_primary:
        return (
            TelegramSourceState.unsupported,
            "This source primarily uses image signals, which are not supported.",
        )
    return TelegramSourceState.ready, None


async def _learn_source(source_id: uuid.UUID) -> None:
    from langchain_openai import ChatOpenAI
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    with SessionLocal() as db:
        source = db.get(TelegramSource, source_id)
        if source is None:
            return
        existing = db.execute(select(ChannelProfile).where(ChannelProfile.telegram_chat_id == source.telegram_chat_id).order_by(ChannelProfile.validated_at.desc())).scalars().first()
        now = datetime.now(timezone.utc)
        if existing and existing.validated_at > now - timedelta(days=1):
            source.profile_id = existing.id
            source.state = TelegramSourceState.unsupported if existing.image_primary else TelegramSourceState.ready
            source.unsupported_reason = "This source primarily uses image signals, which are not supported." if existing.image_primary else None
            db.commit()
            return
        connection = db.get(TelegramConnection, source.connection_id)
        encrypted_session = connection.encrypted_session if connection else None
        chat_id = source.telegram_chat_id
    if not encrypted_session:
        raise RuntimeError("Telegram connection is not authorized.")
    telegram = TelegramClient(StringSession(SessionCipher(settings.ENCRYPTION_KEY).decrypt(encrypted_session)), settings.TELEGRAM_API_ID, settings.TELEGRAM_API_HASH)
    await telegram.connect()
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    samples = []
    image_count = 0
    async for message in telegram.iter_messages(chat_id, offset_date=datetime.now(timezone.utc), reverse=False):
        if message.date.astimezone(timezone.utc) < cutoff:
            break
        text = (message.message or "").strip()
        if message.media:
            image_count += 1
        if text:
            samples.append({"message_id": int(message.id), "text": text[:4000], "date": message.date.astimezone(timezone.utc).isoformat(), "reply_to": getattr(getattr(message, "reply_to", None), "reply_to_msg_id", None), "has_media": bool(message.media)})
        if len(samples) >= 250:
            break
    await telegram.disconnect()
    if not samples:
        result = LearningResult(signal_style="No usable text signals", recommended_assembly_window_seconds=90, confidence=AutomationConfidence.low, confidence_score=0, image_primary=image_count > 0, supported_actions=[], author_pattern={})
    else:
        model = ChatOpenAI(model=settings.COPY_TRADING_LEARNING_MODEL, api_key=settings.OPENAI_API_KEY, timeout=45, max_retries=1).with_structured_output(LearningResult, method="json_schema")
        result = await model.ainvoke([("system", "Analyze this Telegram trading source. Detect complete vs multi-message signals, safe assembly timing, supported actions, author patterns and image dependence. Be conservative. Assembly window must be 1-600 seconds."), ("human", _build_learning_prompt(samples))])
    image_frequency = image_count / max(len(samples) + image_count, 1)
    image_primary = result.image_primary or image_frequency >= 0.5
    now = datetime.now(timezone.utc)
    cipher = SessionCipher(settings.ENCRYPTION_KEY)
    with SessionLocal() as db:
        source = db.get(TelegramSource, source_id)
        profile = db.execute(
            select(ChannelProfile).where(
                ChannelProfile.telegram_chat_id == source.telegram_chat_id,
                ChannelProfile.parser_version == "v1",
            )
        ).scalar_one_or_none()
        if profile is None:
            profile = ChannelProfile(
                telegram_chat_id=source.telegram_chat_id,
                parser_version="v1",
                signal_style=result.signal_style,
                recommended_assembly_window_seconds=90,
                confidence=result.confidence,
                confidence_score=result.confidence_score,
                image_frequency=image_frequency,
                image_primary=image_primary,
                supported_actions=[],
                author_pattern={},
                analyzed_from=now - timedelta(days=7),
                analyzed_to=now,
                sample_count=0,
                validated_at=now,
            )
            db.add(profile)
        profile.signal_style = result.signal_style
        profile.recommended_assembly_window_seconds = max(
            1,
            min(600, result.recommended_assembly_window_seconds),
        )
        profile.confidence = result.confidence
        profile.confidence_score = result.confidence_score
        profile.image_frequency = image_frequency
        profile.image_primary = image_primary
        profile.supported_actions = result.supported_actions
        profile.author_pattern = result.author_pattern
        profile.analyzed_from = now - timedelta(days=7)
        profile.analyzed_to = now
        profile.sample_count = len(samples)
        profile.validated_at = now
        db.flush()
        db.execute(
            delete(ChannelMessageSample).where(
                ChannelMessageSample.profile_id == profile.id
            )
        )
        for sample in samples:
            db.add(ChannelMessageSample(profile_id=profile.id, telegram_chat_id=source.telegram_chat_id, telegram_message_id=sample["message_id"], encrypted_raw_message=cipher.encrypt(sample["text"]), message_metadata={key: value for key, value in sample.items() if key != "text"}, purpose="learning", expires_at=now + timedelta(days=7)))
        source.profile_id = profile.id
        source.state, source.unsupported_reason = _learning_source_outcome(
            confidence=result.confidence,
            image_primary=image_primary,
        )
        db.commit()


def run_process(role: str) -> None:
    if not settings.COPY_TRADING_ENABLED:
        logger.warning("COPY_TRADING_ENABLED is false; worker remains healthy but does not consume actions")
    else:
        logger.info("copy-trading worker started role=%s", role)
    if role == "telegram-session":
        asyncio.run(TelegramSessionRuntime().run())
    elif role == "copy-learning":
        StreamWorker(stream=StreamName.learning_jobs, group="copy-learning", handler=learning_handler).run()
    else:
        from app.domains.copy_trading.workers import execution_handler, signal_handler
        stream, group, handler = {
            "copy-signal": (StreamName.telegram_messages, "copy-signal", signal_handler),
            "copy-execution": (StreamName.execution_intents, "copy-execution", execution_handler),
        }[role]
        StreamWorker(stream=stream, group=group, handler=handler).run()

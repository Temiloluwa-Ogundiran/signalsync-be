import asyncio
import json
import logging
import signal
import socket
import time
import threading
import uuid
import base64
import io
from datetime import datetime, timezone

import redis
from sqlalchemy import select, update

import app.models  # noqa: F401 - register string-based ORM relationships for standalone workers
from app.core.config import settings
from app.core.database import SessionLocal
from app.domains.copy_trading.models import (
    CopyActivityEvent,
    CopyActivityLevel,
    CopyDeadLetter,
    DeadLetterState,
    TelegramConnection,
    TelegramConnectionState,
    TelegramSource,
    TelegramSourceType,
    CopyRoute,
    TelegramAuthAttempt,
    TradeIntent,
)
from app.domains.copy_trading.delivery import (
    DeliveryDisposition,
    DeliveryResult,
    KeyedSerialExecutor,
    normalize_delivery_result,
)
from app.domains.copy_trading.telegram_auth import (
    auth_state_from_public,
    decode_auth_state,
    encode_auth_state,
    image_message_outcome,
    merge_auth_state,
)
from app.domains.copy_trading.health import record_worker_health
from app.domains.copy_trading.security import SessionCipher
from app.domains.copy_trading.streams import CopyEvent, RedisStreamBus, StreamName


logger = logging.getLogger("copy-trading.worker")


class StreamWorker:
    max_attempts = 5
    retry_idle_ms = 5_000

    def __init__(self, *, stream: StreamName, group: str, handler):
        self.client = redis.Redis.from_url(settings.COPY_TRADING_REDIS_URL, decode_responses=True)
        self.bus = RedisStreamBus(self.client)
        self.stream = stream
        self.group = group
        self.consumer = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        self.handler = handler
        self.running = True
        self.last_maintenance_at = 0.0
        self.last_health_at = 0.0
        self.executor = KeyedSerialExecutor(max_workers=8)

    def run(self) -> None:
        self.bus.ensure_group(self.stream, self.group)
        if self.group == "copy-execution":
            from app.domains.copy_trading.metaapi_execution import publish_unresolved_intents
            publish_unresolved_intents(self.client)
        self.client.setex(f"copy:heartbeat:{self.group}:{self.consumer}", 30, datetime.now(timezone.utc).isoformat())
        while self.running:
            self._run_maintenance()
            self._claim_stale_messages()
            rows = self.client.xreadgroup(self.group, self.consumer, {self.stream.value: ">"}, count=10, block=settings.COPY_TRADING_CONSUMER_BLOCK_MS)
            self.client.setex(f"copy:heartbeat:{self.group}:{self.consumer}", 30, datetime.now(timezone.utc).isoformat())
            self._record_health()
            for _, messages in rows:
                for message_id, fields in messages:
                    self.executor.submit(self._ordering_key(fields), self._process_message, message_id, fields)

    def _ordering_key(self, fields: dict) -> str:
        try:
            event = CopyEvent.from_fields(fields)
        except Exception:
            return f"invalid:{uuid.uuid4()}"
        payload = event.payload
        return str(payload.get("source_id") or payload.get("account_id") or payload.get("connection_id") or payload.get("intent_id") or event.idempotency_key)

    def _record_health(self) -> None:
        now = time.monotonic()
        if now - self.last_health_at < 10:
            return
        lag = 0
        pending = 0
        try:
            group = next((item for item in self.client.xinfo_groups(self.stream.value) if item.get("name") == self.group), None)
            if group:
                lag = int(group.get("lag") or 0)
                pending = int(group.get("pending") or 0)
        except Exception:
            logger.exception("Could not read stream health group=%s", self.group)
        record_worker_health(worker_role=self.group, instance_id=self.consumer, stream_lag=lag, pending_count=pending)
        self.last_health_at = now

    def _run_maintenance(self) -> None:
        if self.group not in {"copy-signal", "copy-execution"}:
            return
        now = time.monotonic()
        interval = 5 if self.group == "copy-signal" else 30
        if now - self.last_maintenance_at < interval:
            return
        if self.group == "copy-signal":
            from app.domains.copy_trading.workers import expire_signal_threads

            expired_count = expire_signal_threads()
            if expired_count:
                logger.info("Expired incomplete signal threads count=%s", expired_count)
        else:
            from app.domains.copy_trading.metaapi_execution import (
                publish_unresolved_intents,
                reconcile_copied_trades,
            )

            publish_unresolved_intents(self.client)
            updated = reconcile_copied_trades()
            if updated:
                logger.info("Reconciled copied trade state count=%s", updated)
        self.last_maintenance_at = now

    def _process_message(self, message_id: str, fields: dict) -> None:
        event = CopyEvent.from_fields(fields)
        dedupe_key = f"copy:processed:{self.group}:{event.idempotency_key}"
        try:
            if self.client.get(dedupe_key):
                self.client.xack(self.stream.value, self.group, message_id)
                return
            result = normalize_delivery_result(self.handler(event, self.client))
        except Exception as exc:
            logger.exception(
                "Copy-trading event failed stream=%s group=%s event_type=%s correlation_id=%s",
                self.stream.value,
                self.group,
                event.event_type,
                event.correlation_id,
            )
            result = DeliveryResult.retry(
                exc.__class__.__name__.upper(), str(exc)
            )

        attempts = self._pending_attempts(message_id)
        if result.disposition == DeliveryDisposition.retry and attempts < self.max_attempts:
            return
        if result.disposition in {
            DeliveryDisposition.retry,
            DeliveryDisposition.dead_letter,
        }:
            self._persist_dead_letter(
                event=event,
                message_id=message_id,
                attempts=attempts,
                error_code=result.error_code or "DELIVERY_FAILED",
                error_message=result.message or "Event processing failed.",
            )
            self.client.xack(self.stream.value, self.group, message_id)
            return

        self.client.setex(dedupe_key, 604800, event.event_id)
        self.client.xack(self.stream.value, self.group, message_id)

    def _pending_attempts(self, message_id: str) -> int:
        try:
            rows = self.client.xpending_range(
                self.stream.value,
                self.group,
                min=message_id,
                max=message_id,
                count=1,
            )
        except Exception:
            return 1
        if not rows:
            return 1
        row = rows[0]
        return int(row.get("times_delivered", row.get("delivery_count", 1)))

    def _persist_dead_letter(
        self,
        *,
        event: CopyEvent,
        message_id: str,
        attempts: int,
        error_code: str,
        error_message: str,
    ) -> None:
        user_id = event.payload.get("user_id")
        with SessionLocal() as db:
            if not user_id and event.payload.get("source_id"):
                source = db.get(TelegramSource, uuid.UUID(event.payload["source_id"]))
                user_id = str(source.user_id) if source else None
            if not user_id and event.payload.get("intent_id"):
                intent = db.get(TradeIntent, uuid.UUID(event.payload["intent_id"]))
                user_id = str(intent.user_id) if intent else None
            db.add(
                CopyDeadLetter(
                    user_id=uuid.UUID(user_id) if user_id else None,
                    source_stream=self.stream.value,
                    consumer_group=self.group,
                    source_message_id=message_id,
                    event_type=event.event_type,
                    correlation_id=event.correlation_id,
                    idempotency_key=event.idempotency_key,
                    event_payload=event.to_fields(),
                    attempts=attempts,
                    error_code=error_code,
                    error_message=error_message,
                    state=DeadLetterState.pending,
                )
            )
            db.commit()

    def _claim_stale_messages(self) -> None:
        try:
            claimed = self.client.xautoclaim(
                self.stream.value,
                self.group,
                self.consumer,
                min_idle_time=self.retry_idle_ms,
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
        self.last_health_at = 0.0
        self.last_connection_heartbeat_at = 0.0
        # Connection ids with a dialog refresh already running — prevents
        # overlapping iter_dialogs() walks (each is several GetDialogsRequest
        # calls) from stacking up and tripping Telegram's flood limit.
        self.dialog_refresh_inflight: set[str] = set()

    def _refresh_connection_heartbeats(self) -> None:
        connection_ids = [
            uuid.UUID(key.removeprefix("connection:"))
            for key in self.clients
            if key.startswith("connection:")
        ]
        if not connection_ids:
            return
        with SessionLocal() as db:
            db.execute(
                update(TelegramConnection)
                .where(TelegramConnection.id.in_(connection_ids))
                .values(last_heartbeat_at=datetime.now(timezone.utc))
            )
            db.commit()

    def _auth_update(self, auth_id: str, **values) -> None:
        with SessionLocal() as db:
            attempt = db.execute(select(TelegramAuthAttempt).where(TelegramAuthAttempt.auth_id == auth_id)).scalar_one_or_none()
            if attempt:
                current = decode_auth_state(attempt, self.cipher)
                merged = merge_auth_state(current, values)
                attempt.encrypted_state = encode_auth_state(merged, self.cipher)
                public_state = str(values.get("state", merged.get("state", "starting")))
                attempt.state = auth_state_from_public(public_state)
                attempt.message = values.get("message", attempt.message)
                db.commit()
        key = f"copy:telegram:auth:{auth_id}"
        self.redis.hset(key, mapping={name: str(value) for name, value in values.items() if value is not None})
        self.redis.expire(key, 600)

    async def _finalize(self, auth_id: str, client) -> None:
        me = await client.get_me()
        telegram_user_id = int(me.id)
        display_name = " ".join(part for part in (getattr(me, "first_name", None), getattr(me, "last_name", None)) if part) or None
        username = getattr(me, "username", None)
        from telethon.sessions import StringSession

        session_value = StringSession.save(client.session)
        encrypted_session = self.cipher.encrypt(session_value)
        now = datetime.now(timezone.utc)

        # The live connection id may differ from auth_id: if this user already
        # has a connection for this Telegram account (UNIQUE(user_id,
        # telegram_user_id)), re-auth must adopt the fresh session into that
        # existing row — it is the one telegram_sources/copy_routes reference.
        # Committing the new row's telegram_user_id would otherwise violate the
        # unique constraint and leave the connection stuck.
        connection_id = auth_id
        with SessionLocal() as db:
            connection = db.get(TelegramConnection, uuid.UUID(auth_id))
            if connection is None:
                await client.disconnect()
                return

            existing = db.execute(
                select(TelegramConnection).where(
                    TelegramConnection.user_id == connection.user_id,
                    TelegramConnection.telegram_user_id == telegram_user_id,
                    TelegramConnection.id != connection.id,
                )
            ).scalar_one_or_none()

            target = existing or connection
            target.telegram_user_id = telegram_user_id
            target.display_name = display_name
            target.username = username
            target.encrypted_session = encrypted_session
            target.state = TelegramConnectionState.ready
            target.reauthentication_reason = None
            target.last_heartbeat_at = now

            if existing is not None:
                # Drop the throwaway row created for this auth attempt so we
                # don't leak orphan pending connections.
                db.delete(connection)

            db.commit()
            connection_id = str(target.id)

        self._auth_update(auth_id, state="ready", message="Telegram connected", connection_id=connection_id)
        await self._attach_updates(connection_id, client)
        asyncio.create_task(self._refresh_dialog_cache(connection_id, client))

    # How long a cached dialog list is considered fresh. Within this window a
    # dialogs.refresh request serves the cache without re-walking iter_dialogs.
    DIALOG_CACHE_TTL_SECONDS = 60

    def _dialog_cache_is_fresh(self, connection_id: str) -> bool:
        return bool(self.redis.get(f"copy:telegram:dialogs-fresh:{connection_id}"))

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
        # Freshness marker drives the throttle in _refresh_dialog_cache; the
        # dialog list itself lives much longer so we can always serve stale.
        self.redis.setex(f"copy:telegram:dialogs-fresh:{connection_id}", self.DIALOG_CACHE_TTL_SECONDS, "1")
        return dialogs

    async def _refresh_dialog_cache(self, connection_id: str, client, *, force: bool = False) -> None:
        from telethon.errors import FloodWaitError

        # Serve-cache throttle: skip the (expensive, flood-prone) walk when a
        # recent refresh already populated the cache.
        if not force and self._dialog_cache_is_fresh(connection_id):
            return
        # Coalesce: only one walk per connection at a time.
        if connection_id in self.dialog_refresh_inflight:
            return
        self.dialog_refresh_inflight.add(connection_id)
        try:
            await self._cache_dialogs(connection_id, client)
        except FloodWaitError as exc:
            # Don't retry into the limit — mark fresh for the backoff window so
            # callers serve stale cache instead of piling on more requests.
            logger.warning(
                "Telegram dialog refresh flood-limited connection_id=%s seconds=%s",
                connection_id,
                getattr(exc, "seconds", "?"),
            )
            self.redis.setex(
                f"copy:telegram:dialogs-fresh:{connection_id}",
                max(self.DIALOG_CACHE_TTL_SECONDS, int(getattr(exc, "seconds", 0) or 0)),
                "1",
            )
        except Exception:
            logger.exception(
                "Could not refresh Telegram dialogs connection_id=%s",
                connection_id,
            )
        finally:
            self.dialog_refresh_inflight.discard(connection_id)

    async def _attach_updates(self, connection_id: str, client) -> None:
        from telethon import events

        # Re-auth replaces the session for an already-attached connection. Drop
        # the prior client first so we don't leak it (its old auth key is now
        # revoked, which otherwise surfaces as AuthKeyUnregisteredError spam).
        previous = self.clients.pop(f"connection:{connection_id}", None)
        if previous is not None and previous is not client:
            try:
                await previous.disconnect()
            except Exception:
                logger.warning("Could not disconnect stale Telegram client connection_id=%s", connection_id, exc_info=True)

        async def publish_message(event_type: str, event) -> None:
            chat_id = int(event.chat_id)
            message = event.message
            text = message.message or ""
            with SessionLocal() as db:
                source = db.execute(select(TelegramSource).where(TelegramSource.connection_id == uuid.UUID(connection_id), TelegramSource.telegram_chat_id == chat_id)).scalar_one_or_none()
                connection = db.get(TelegramConnection, uuid.UUID(connection_id))
                if source is None or connection is None or connection.is_paused or source.is_paused:
                    return
                if not text.strip() and message.media:
                    counter_key = f"copy:telegram:image-only:{source.id}"
                    count = int(self.redis.incr(counter_key))
                    self.redis.expire(counter_key, 86400)
                    outcome = image_message_outcome()
                    routes = list(db.execute(select(CopyRoute).where(CopyRoute.source_id == source.id)).scalars())
                    for route in routes:
                        db.add(CopyActivityEvent(user_id=route.user_id, route_id=route.id, source_id=source.id, connection_id=route.target_connection_id, correlation_id=str(uuid.uuid4()), action="source.image_message", level=CopyActivityLevel.warning, title="Image signal skipped", body=outcome.message, parsed_details={"recent_image_only_count": count}, broker_details={}))
                    db.commit()
                    return
                source_id = str(source.id)
                source_type = source.source_type
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

    async def _restore_auth_client(self, auth_id: str):
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        with SessionLocal() as db:
            attempt = db.execute(select(TelegramAuthAttempt).where(TelegramAuthAttempt.auth_id == auth_id)).scalar_one()
            state = decode_auth_state(attempt, self.cipher)
        session_value = state.get("session")
        if not session_value:
            raise RuntimeError("Telegram sign-in session expired. Start again.")
        client = TelegramClient(StringSession(session_value), settings.TELEGRAM_API_ID, settings.TELEGRAM_API_HASH)
        await client.connect()
        restored = (client, state.get("phone"), state.get("phone_code_hash"))
        self.clients[auth_id] = restored
        return restored

    async def handle(self, event: CopyEvent) -> None:
        from telethon.errors import SessionPasswordNeededError

        auth_id = event.payload.get("auth_id")
        if event.event_type == "auth.phone.start":
            client = await self._new_client()
            await client.connect()
            sent = await client.send_code_request(event.payload["phone"])
            self.clients[auth_id] = (client, event.payload["phone"], sent.phone_code_hash)
            from telethon.sessions import StringSession
            self._auth_update(auth_id, state="code_required", message="Enter the code Telegram sent", phone=event.payload["phone"], phone_code_hash=sent.phone_code_hash, session=StringSession.save(client.session))
        elif event.event_type == "auth.phone.code":
            client, phone, code_hash = self.clients.get(auth_id) or await self._restore_auth_client(auth_id)
            try:
                await client.sign_in(phone=phone, code=self.cipher.decrypt(event.payload["code_encrypted"]), phone_code_hash=code_hash)
            except SessionPasswordNeededError:
                self._auth_update(auth_id, state="password_required", message="Enter your Telegram two-step password")
                return
            await self._finalize(auth_id, client)
        elif event.event_type == "auth.phone.password":
            client, _, _ = self.clients.get(auth_id) or await self._restore_auth_client(auth_id)
            # On a retry the client may already be authorized — re-signing in
            # with the (possibly already-cleared) password would raise
            # InvalidToken. Only submit the password when still needed.
            if not await client.is_user_authorized():
                await client.sign_in(password=self.cipher.decrypt(event.payload["password_encrypted"]))
            event.payload["password_encrypted"] = ""
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
            from telethon.sessions import StringSession
            self._auth_update(auth_id, state="qr_required", qr_url=qr_data_url, session=StringSession.save(client.session), message="Scan this QR code in Telegram")
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
            cached = self.redis.get(f"copy:telegram:dialogs:{connection_id}")
            self.redis.setex(
                response_key,
                30,
                cached or "[]",
            )
            asyncio.create_task(self._refresh_dialog_cache(connection_id, client))
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
                await self._attach_updates(str(connection.id), client)
                asyncio.create_task(
                    self._refresh_dialog_cache(str(connection.id), client)
                )
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
            now = time.monotonic()
            if now - self.last_health_at >= 10:
                await asyncio.to_thread(record_worker_health, worker_role=group, instance_id=consumer)
                self.last_health_at = now
            if now - self.last_connection_heartbeat_at >= 30:
                await asyncio.to_thread(self._refresh_connection_heartbeats)
                self.last_connection_heartbeat_at = now
            for _, messages in rows:
                for message_id, fields in messages:
                    event = CopyEvent.from_fields(fields)
                    try:
                        await self.handle(event)
                    except Exception as exc:
                        logger.exception("Telegram command failed", extra={"correlation_id": event.correlation_id})
                        attempt = int(event.payload.get("_attempt", 1))
                        if attempt < 5:
                            retry_payload = dict(event.payload)
                            retry_payload["_attempt"] = attempt + 1
                            self.bus.publish(
                                CopyEvent.new(
                                    stream=StreamName.telegram_commands,
                                    event_type=event.event_type,
                                    correlation_id=event.correlation_id,
                                    payload=retry_payload,
                                    idempotency_key=f"{event.idempotency_key}:retry:{attempt + 1}",
                                )
                            )
                        elif event.payload.get("auth_id"):
                            self._auth_update(event.payload["auth_id"], state="failed", message=f"Telegram sign-in failed: {exc}")
                    finally:
                        self.redis.xack(StreamName.telegram_commands.value, group, message_id)
                        self.redis.xdel(StreamName.telegram_commands.value, message_id)


def run_process(role: str) -> None:
    if not settings.COPY_TRADING_ENABLED:
        logger.warning("COPY_TRADING_ENABLED is false; worker remains healthy but does not consume actions")
    else:
        logger.info("copy-trading worker started role=%s", role)
    if role == "telegram-session":
        asyncio.run(TelegramSessionRuntime().run())
    else:
        from app.domains.copy_trading.metaapi_execution import execution_handler
        from app.domains.copy_trading.workers import signal_handler
        provisioning_worker = None
        if role == "copy-execution":
            from app.domains.copy_trading.metaapi_jobs import provisioning_handler

            provisioning_worker = StreamWorker(
                stream=StreamName.metaapi_provisioning,
                group="copy-provisioning",
                handler=provisioning_handler,
            )
            provisioning_worker.retry_idle_ms = max(
                60_000,
                (settings.METAAPI_CONNECTION_TIMEOUT_SECONDS * 3 + 30) * 1000,
            )
            threading.Thread(
                target=provisioning_worker.run,
                name="copy-provisioning",
                daemon=True,
            ).start()
        stream, group, handler = {
            "copy-signal": (StreamName.telegram_messages, "copy-signal", signal_handler),
            "copy-execution": (StreamName.execution_intents, "copy-execution", execution_handler),
        }[role]
        worker = StreamWorker(stream=stream, group=group, handler=handler)

        def stop_workers(*_args) -> None:
            worker.running = False
            if provisioning_worker is not None:
                provisioning_worker.running = False

        signal.signal(signal.SIGTERM, stop_workers)
        signal.signal(signal.SIGINT, stop_workers)
        try:
            worker.run()
        finally:
            worker.executor.shutdown(wait=True)
            if provisioning_worker is not None:
                provisioning_worker.executor.shutdown(wait=True)
                from app.domains.copy_trading.metaapi_connections import (
                    shutdown_metaapi_runtime,
                )

                shutdown_metaapi_runtime()

import asyncio
import threading
import time
from dataclasses import dataclass
from typing import Callable


@dataclass
class _ConnectionEntry:
    connection: object
    last_used_at: float


class MetaApiConnectionManager:
    def __init__(
        self,
        *,
        api,
        timeout_seconds: int,
        idle_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api = api
        self._timeout_seconds = timeout_seconds
        self._idle_seconds = idle_seconds
        self._clock = clock
        self._entries: dict[str, _ConnectionEntry] = {}
        self._opening: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    @property
    def warm_count(self) -> int:
        return len(self._entries)

    async def acquire(self, account_id: str):
        async with self._lock:
            entry = self._entries.get(account_id)
            if entry is not None:
                entry.last_used_at = self._clock()
                return entry.connection
            opening = self._opening.get(account_id)
            if opening is None:
                opening = asyncio.create_task(self._open(account_id))
                self._opening[account_id] = opening
        return await opening

    async def _open(self, account_id: str):
        try:
            account = await self._api.metatrader_account_api.get_account(account_id)
            connection = account.get_streaming_connection()
            await connection.connect()
            await connection.wait_synchronized(
                {"timeoutInSeconds": self._timeout_seconds}
            )
            async with self._lock:
                self._entries[account_id] = _ConnectionEntry(
                    connection=connection, last_used_at=self._clock()
                )
            return connection
        finally:
            async with self._lock:
                self._opening.pop(account_id, None)

    async def mark_unhealthy(self, account_id: str) -> None:
        async with self._lock:
            entry = self._entries.pop(account_id, None)
        if entry is not None:
            await entry.connection.close()

    async def close_account(self, account_id: str) -> None:
        await self.mark_unhealthy(account_id)

    async def evict_idle(self) -> int:
        cutoff = self._clock() - self._idle_seconds
        async with self._lock:
            expired = [
                account_id
                for account_id, entry in self._entries.items()
                if entry.last_used_at < cutoff
            ]
            entries = [self._entries.pop(account_id) for account_id in expired]
        for entry in entries:
            await entry.connection.close()
        return len(entries)

    async def shutdown(self) -> None:
        async with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
            opening = list(self._opening.values())
            self._opening.clear()
        for task in opening:
            task.cancel()
        if opening:
            await asyncio.gather(*opening, return_exceptions=True)
        for entry in entries:
            await entry.connection.close()


class MetaApiRuntime:
    def __init__(
        self,
        *,
        api_factory,
        timeout_seconds: int,
        idle_seconds: int,
    ) -> None:
        self._api_factory = api_factory
        self._timeout_seconds = timeout_seconds
        self._idle_seconds = idle_seconds
        self._loop: asyncio.AbstractEventLoop | None = None
        self._manager: MetaApiConnectionManager | None = None
        self._api = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._start_lock = threading.Lock()
        self._startup_error: BaseException | None = None

    def start(self) -> None:
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._ready.clear()
            self._startup_error = None
            self._thread = threading.Thread(
                target=self._run,
                name="metaapi-streaming-loop",
                daemon=True,
            )
            self._thread.start()
        if not self._ready.wait(timeout=self._timeout_seconds):
            raise TimeoutError("MetaApi runtime did not start in time.")
        if self._startup_error is not None:
            raise RuntimeError("MetaApi runtime failed to start.") from self._startup_error

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop

        async def initialize() -> None:
            self._api = self._api_factory()
            self._manager = MetaApiConnectionManager(
                api=self._api,
                timeout_seconds=self._timeout_seconds,
                idle_seconds=self._idle_seconds,
            )

        try:
            loop.run_until_complete(initialize())
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
            loop.close()
            return
        self._ready.set()
        loop.run_forever()
        loop.close()

    def _submit(self, coroutine):
        self.start()
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        return future.result(timeout=self._timeout_seconds + 5)

    def acquire(self, account_id: str):
        assert self._manager is not None or self._thread is None
        self.start()
        assert self._manager is not None
        self._submit(self._manager.evict_idle())
        return self._submit(self._manager.acquire(account_id))

    def run(self, coroutine):
        return self._submit(coroutine)

    def close_account(self, account_id: str) -> None:
        if self._manager is not None:
            self._submit(self._manager.close_account(account_id))

    def shutdown(self) -> None:
        if self._thread is None or not self._thread.is_alive() or self._loop is None:
            return

        async def cleanup() -> None:
            if self._manager is not None:
                await self._manager.shutdown()
            close = getattr(self._api, "close", None)
            if close is not None:
                close()
                await asyncio.sleep(0)

        self._submit(cleanup())
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        self._thread = None
        self._manager = None
        self._loop = None


_runtime: MetaApiRuntime | None = None
_runtime_lock = threading.Lock()


def get_metaapi_runtime() -> MetaApiRuntime:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            from app.core.config import settings
            from app.domains.copy_trading.metaapi_client import build_metaapi

            _runtime = MetaApiRuntime(
                api_factory=lambda: build_metaapi(
                    settings.METAAPI_TOKEN, region=settings.METAAPI_REGION
                ),
                timeout_seconds=settings.METAAPI_CONNECTION_TIMEOUT_SECONDS,
                idle_seconds=settings.METAAPI_IDLE_CONNECTION_SECONDS,
            )
        return _runtime


def shutdown_metaapi_runtime() -> None:
    global _runtime
    with _runtime_lock:
        runtime = _runtime
        _runtime = None
    if runtime is not None:
        runtime.shutdown()

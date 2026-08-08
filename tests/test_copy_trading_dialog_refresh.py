import json
import time
import uuid

from app.domains.copy_trading import router


class FakeRedis:
    def __init__(self, cached: list[dict]):
        self.cached = json.dumps(cached)

    def get(self, key: str):
        if key.startswith("copy:telegram:dialogs-response:"):
            return None
        return self.cached

    def delete(self, _key: str):
        return None


def test_forced_dialog_refresh_returns_stale_cache_without_blocking(monkeypatch) -> None:
    cached = [{"chat_id": 1, "title": "Signals", "source_type": "channel"}]
    monkeypatch.setattr(router, "_publish_command", lambda *args, **kwargs: None)
    started = time.monotonic()

    result = router._request_live_dialogs(
        FakeRedis(cached),
        uuid.uuid4(),
        timeout_seconds=50,
        force_refresh=True,
    )

    assert result == cached
    assert time.monotonic() - started < 0.25

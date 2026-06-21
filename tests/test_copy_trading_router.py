import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.domains.copy_trading.models import TelegramSourceState, TelegramSourceType
from app.domains.copy_trading.router import _request_live_dialogs, update_source_pause
from app.domains.copy_trading.schemas import CopyTradingSettingsUpdate
from app.shared.deps import get_current_user


def test_copy_trading_routes_require_authentication() -> None:
    response = TestClient(app).get("/copy-trading/routes")
    assert response.status_code == 401


@patch("app.domains.copy_trading.router.service.list_routes", return_value=[])
def test_list_routes_uses_authenticated_user(list_routes) -> None:
    user = MagicMock(id=uuid.uuid4())
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        response = TestClient(app).get("/copy-trading/routes")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == []
    assert list_routes.call_args.kwargs["current_user"] is user


def test_openapi_does_not_expose_telegram_session_mutation() -> None:
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    assert "/copy-trading/routes" in paths
    assert "/copy-trading/telegram-connections" not in paths


@patch("app.domains.copy_trading.router.repo")
def test_source_pause_does_not_bypass_unsupported_learning_result(repo) -> None:
    user = MagicMock(id=uuid.uuid4())
    source = SimpleNamespace(
        id=uuid.uuid4(),
        connection_id=uuid.uuid4(),
        telegram_chat_id=-100123,
        title="Signals",
        username=None,
        source_type=TelegramSourceType.group,
        state=TelegramSourceState.unsupported,
        unsupported_reason="Images are not supported.",
        is_paused=False,
        profile_id=None,
        profile=None,
    )
    repo.get_source_for_user.return_value = source
    db = MagicMock()

    update_source_pause(
        source.id,
        CopyTradingSettingsUpdate(is_paused=True),
        db,
        user,
    )
    update_source_pause(
        source.id,
        CopyTradingSettingsUpdate(is_paused=False),
        db,
        user,
    )

    assert source.state == TelegramSourceState.unsupported
    assert source.is_paused is False


@patch("app.domains.copy_trading.router._publish_command")
def test_live_dialog_request_uses_telegram_session_worker(publish_command) -> None:
    connection_id = uuid.uuid4()
    client = MagicMock()
    client.get.return_value = (
        '[{"chat_id":-1001,"title":"New group","username":null,'
        '"source_type":"group","is_admin":false}]'
    )

    dialogs = _request_live_dialogs(
        client,
        connection_id,
        timeout_seconds=0.1,
    )

    assert dialogs[0]["title"] == "New group"
    event_type, correlation_id, payload, key = publish_command.call_args.args
    assert event_type == "dialogs.refresh"
    assert correlation_id == payload["request_id"]
    assert payload["connection_id"] == str(connection_id)
    assert key == f"dialogs-refresh:{payload['request_id']}"

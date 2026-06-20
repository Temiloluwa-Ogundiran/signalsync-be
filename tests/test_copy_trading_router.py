import uuid
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
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

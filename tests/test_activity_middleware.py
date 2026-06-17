import uuid
import time
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

# Satisfy sqlalchemy mapper dependencies in tests
import app.domains.journal.models  # noqa: F401
import app.domains.auth.models  # noqa: F401

from app.shared.activity import _should_touch, AuthActivityMiddleware


def test_should_touch_cooldown() -> None:
    user_id = uuid.uuid4()
    
    # 1. First touch: should return True
    assert _should_touch(user_id, time.monotonic()) is True
    
    # 2. Immediate second touch: should return False (within 60s cooldown)
    assert _should_touch(user_id, time.monotonic()) is False
    
    # 3. Touch after cooldown: simulate time passing
    assert _should_touch(user_id, time.monotonic() + 61) is True


@pytest.mark.anyio
@patch("app.shared.activity.decode_token")
@patch("app.shared.activity._should_touch")
@patch("app.shared.activity.asyncio.get_running_loop")
async def test_activity_middleware_dispatch(
    mock_get_loop,
    mock_should_touch,
    mock_decode_token,
) -> None:
    mock_decode_token.return_value = {"sub": str(uuid.uuid4())}
    mock_should_touch.return_value = True
    
    mock_loop = MagicMock()
    mock_get_loop.return_value = mock_loop
    
    # Mock request and next handler
    request = MagicMock()
    request.headers = {"Authorization": "Bearer some-token"}
    
    response_mock = MagicMock()
    
    async def call_next(req):
        return response_mock
        
    middleware = AuthActivityMiddleware(app=MagicMock())
    
    # Execute
    res = await middleware.dispatch(request, call_next)
    
    assert res is response_mock
    mock_should_touch.assert_called_once()
    mock_loop.run_in_executor.assert_called_once()

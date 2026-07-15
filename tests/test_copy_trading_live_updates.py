from types import SimpleNamespace
from unittest.mock import MagicMock
import uuid

from app.domains.copy_trading.live_updates import _USER_MODELS
from app.domains.copy_trading.live_updates import _update_user_id
from app.domains.copy_trading.models import CopiedTrade, CopyRoute


def test_broker_reconciliation_changes_trigger_realtime_ui_updates() -> None:
    assert CopiedTrade in _USER_MODELS

    user_id = uuid.uuid4()
    route_id = uuid.uuid4()
    session = MagicMock()
    session.get.return_value = SimpleNamespace(user_id=user_id)
    trade = CopiedTrade(route_id=route_id)

    assert _update_user_id(session, trade) == user_id
    session.get.assert_called_once_with(CopyRoute, route_id)

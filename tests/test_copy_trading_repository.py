import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import app.models  # noqa: F401 - configure the complete SQLAlchemy registry
from app.domains.copy_trading import repository


def compiled_values(statement) -> list[object]:
    return list(statement.compile().params.values())


def test_get_route_for_user_filters_by_route_and_owner() -> None:
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = None
    route_id = uuid.uuid4()
    user_id = uuid.uuid4()

    repository.get_route_for_user(db, route_id=route_id, user_id=user_id)

    values = compiled_values(db.execute.call_args.args[0])
    assert route_id in values
    assert user_id in values


def test_get_source_for_user_filters_by_source_and_owner() -> None:
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = None
    source_id = uuid.uuid4()
    user_id = uuid.uuid4()

    repository.get_source_for_user(db, source_id=source_id, user_id=user_id)

    values = compiled_values(db.execute.call_args.args[0])
    assert source_id in values
    assert user_id in values


def test_list_activity_filters_owner_cursor_and_limit() -> None:
    db = MagicMock()
    db.execute.return_value.scalars.return_value.all.return_value = []
    user_id = uuid.uuid4()
    before = datetime(2026, 6, 20, tzinfo=timezone.utc)

    repository.list_activity_for_user(db, user_id=user_id, limit=50, before=before)

    statement = db.execute.call_args.args[0]
    values = compiled_values(statement)
    assert user_id in values
    assert before in values
    assert 50 in values


def test_create_activity_flushes_without_committing() -> None:
    db = MagicMock()
    event = MagicMock()

    assert repository.create_activity(db, event=event) is event
    db.add.assert_called_once_with(event)
    db.flush.assert_called_once_with()
    db.commit.assert_not_called()

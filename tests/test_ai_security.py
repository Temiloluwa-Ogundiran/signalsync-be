import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.database import Base
from app.domains.ai.service import _chat, _sessions
from app.domains.ai.tools import trade_query


class FakeDb:
    def commit(self) -> None:
        pass


class FakeSessionLocal:
    def __enter__(self) -> FakeDb:
        return FakeDb()

    def __exit__(self, exc_type, exc, tb) -> None:
        pass


def test_create_session_rejects_account_id_not_owned_by_user(monkeypatch) -> None:
    user_id = uuid.uuid4()
    foreign_account_id = uuid.uuid4()

    monkeypatch.setattr(_sessions.repo, "get_accounts_for_user", lambda db, *, user_id: [])
    monkeypatch.setattr(
        _sessions.repo,
        "create_session",
        lambda *args, **kwargs: SimpleNamespace(id=uuid.uuid4()),
    )

    with pytest.raises(HTTPException) as exc_info:
        _sessions.create_session(
            FakeDb(),
            user_id=user_id,
            account_id=foreign_account_id,
        )

    assert exc_info.value.status_code == 404


def test_prepare_turn_rejects_existing_session_scoped_to_foreign_account(monkeypatch) -> None:
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    owned_account_id = uuid.uuid4()
    foreign_account_id = uuid.uuid4()
    session = SimpleNamespace(
        account_id=foreign_account_id,
        context_type="general",
        context_ref=None,
    )

    monkeypatch.setattr(_chat, "SessionLocal", lambda: FakeSessionLocal())
    monkeypatch.setattr(_chat.repo, "get_session", lambda db, *, session_id, user_id: session)
    monkeypatch.setattr(_chat.repo, "auto_title_session", lambda db, *, session, content: None)
    monkeypatch.setattr(_chat.repo, "create_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(_chat.repo, "touch_session", lambda db, *, session, now: None)
    monkeypatch.setattr(
        _chat.repo,
        "get_accounts_for_user",
        lambda db, *, user_id: [{"id": str(owned_account_id), "label": "Owned"}],
    )
    monkeypatch.setattr(_chat, "get_memory_block", lambda db, *, user_id: "")

    with pytest.raises(HTTPException) as exc_info:
        _chat.prepare_turn(session_id, user_id, "Summarize this account")

    assert exc_info.value.status_code == 404


def test_query_trades_intersects_model_account_ids_with_server_scope(monkeypatch) -> None:
    owned_account_id = str(uuid.uuid4())
    foreign_account_id = str(uuid.uuid4())
    captured: dict[str, list[str]] = {}

    class FakeChain:
        def invoke(self, payload):
            return SimpleNamespace(content="SELECT COUNT(*) FROM scoped_trades")

    def fake_execute(model_sql: str, account_ids: list[str]) -> str:
        captured["account_ids"] = account_ids
        return "ok"

    monkeypatch.setattr(trade_query, "_get_chain", lambda: FakeChain())
    monkeypatch.setattr(trade_query, "_execute", fake_execute)

    result = trade_query.query_trades.invoke(
        {
            "question": "How many trades?",
            "account_ids": [foreign_account_id, owned_account_id],
        },
        config={"configurable": {"account_ids": [owned_account_id]}},
    )

    assert result == "ok"
    assert captured["account_ids"] == [owned_account_id]


def test_quota_check_falls_back_to_db_when_redis_is_unavailable(monkeypatch) -> None:
    user_id = uuid.uuid4()
    period_row = SimpleNamespace(credits_used=10_000)

    monkeypatch.setattr(_sessions.repo, "get_accounts_for_user", lambda db, *, user_id: [])
    from app.domains.ai import quota

    monkeypatch.setattr(quota.settings, "AI_ENABLED", True)
    monkeypatch.setitem(quota.PLAN_CREDITS, "free", 1)
    monkeypatch.setattr(quota, "get_redis", lambda: (_ for _ in ()).throw(RuntimeError("redis down")))
    monkeypatch.setattr(quota, "SessionLocal", lambda: FakeSessionLocal())
    monkeypatch.setattr(
        quota.ai_repo,
        "get_or_create_usage",
        lambda db, *, user_id, period_month: period_row,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(quota.check(user_id))

    assert exc_info.value.status_code == 402


def test_app_models_imports_ai_models_for_alembic_metadata() -> None:
    import app.models as models

    assert "AiChatSession" in models.__all__
    assert "ai_chat_sessions" in Base.metadata.tables

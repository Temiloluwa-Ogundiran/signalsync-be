"""
Turn orchestration for the AI chat service.

prepare_turn: load session + account IDs, persist user message, build LangGraph config.
persist_assistant_turn: write assistant message + update session metadata + debit quota.

Both functions manage their own SessionLocal() so they can be called from
anyio.to_thread.run_sync in the streaming path without holding a session across
an LLM call.
"""
import math
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException, status

from app.core.database import SessionLocal
from app.domains.ai import repository as repo
from app.domains.ai.models import AiMessageRole
from app.domains.ai.service._memory import get_memory_block


def prepare_turn(
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    content: str,
) -> Tuple[List[str], str, Dict]:
    """
    1. Verify session ownership.
    2. Persist user message.
    3. Resolve account_ids.
    4. Build LangGraph configurable dict.

    Returns (account_ids, context_block, langgraph_config).
    """
    with SessionLocal() as db:
        session = repo.get_session(db, session_id=session_id, user_id=user_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")

        all_accounts = repo.get_accounts_for_user(db, user_id=user_id)
        # id → human label, e.g. {"abc-123": "demo1"}
        account_map = {a["id"]: a["label"] for a in all_accounts}

        # If the session is scoped to a specific account, honour that scope.
        # Only fall back to all accounts when the session has no account_id.
        if session.account_id:
            if str(session.account_id) not in account_map:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
            account_ids = [str(session.account_id)]
        else:
            account_ids = [a["id"] for a in all_accounts]

        repo.auto_title_session(db, session=session, content=content)
        repo.create_message(
            db,
            session_id=session_id,
            role=AiMessageRole.user,
            content=content,
        )
        now = datetime.now(timezone.utc)
        repo.touch_session(db, session=session, now=now)

        memory_block = get_memory_block(db, user_id=user_id)

        # Capture ORM attributes while the session is still open — after the
        # `with` block closes, `session` is detached and any attribute access
        # raises DetachedInstanceError (commit also expires them).
        context_type = session.context_type
        context_ref = session.context_ref
        session_account_id = session.account_id

        db.commit()

    context_block = ""
    if session_account_id:
        scoped_label = account_map.get(str(session_account_id), str(session_account_id))
        context_block = (
            f"Account scope: this conversation is pinned to '{scoped_label}' (ID: {session_account_id}). "
            f"Pass ONLY that account ID when calling tools. "
            f"Refer to it as '{scoped_label}' in all responses."
        )
    if context_type != "general":
        scope_sentence = (
            f"Context: this conversation was opened from {context_type} "
            f"(ref: {context_ref or 'n/a'}). "
            f"Scope your analysis to this context unless the user asks otherwise."
        )
        context_block = (context_block + "\n\n" + scope_sentence).strip()
    if memory_block:
        context_block += f"\n\n{memory_block}"

    lg_config = {
        "configurable": {
            "thread_id": f"{user_id}_{session_id}",
            "account_ids": account_ids,
            "account_map": account_map,
            "context_block": context_block,
        }
    }
    return account_ids, context_block, lg_config


def persist_assistant_turn(
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    content: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> uuid.UUID:
    """Persist assistant message, update session, debit quota. Returns new message ID."""
    credits = max(1, math.ceil(output_tokens / 500) + 1)

    with SessionLocal() as db:
        msg = repo.create_message(
            db,
            session_id=session_id,
            role=AiMessageRole.assistant,
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        session = repo.get_session(db, session_id=session_id, user_id=user_id)
        if session:
            repo.touch_session(db, session=session, now=datetime.now(timezone.utc))
        db.commit()
        msg_id = msg.id

    return msg_id

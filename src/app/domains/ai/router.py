"""
AI domain router — /ai/* endpoints.

Request path is fully async:
- CRUD endpoints use sync sessions but are I/O-fast.
- The stream endpoint uses astream_events (non-blocking LLM loop) + anyio.to_thread
  for the sync DB prep/persist calls, so a 15-second LLM call never pins a worker.
"""
import json
import logging
import uuid
from typing import List, Optional

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.rate_limit import limiter
from app.domains.ai import repository as repo
from app.domains.ai import service
from app.domains.ai.agent import get_compiled
from app.domains.ai.quota import check as quota_check, debit as quota_debit, get_usage_response
from app.domains.ai.safety import internal_disclosure_response
from app.domains.ai.schemas import (
    CoachReadResponse,
    TradeReviewResponse,
    InsightResponse,
    MessageRequest,
    MessageResponse,
    SessionCreateRequest,
    SessionListResponse,
    SessionResponse,
    SessionWithMessagesResponse,
    SuggestedPromptsResponse,
    UsageResponse,
)
from app.domains.users.models import User
from app.shared.deps import get_current_user, require_journal_access

logger = logging.getLogger("synctrades.ai.router")

router = APIRouter(prefix="/ai", tags=["ai"], dependencies=[Depends(require_journal_access)])


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


def _read_usage(meta) -> tuple[int, int]:
    """Extract (input_tokens, output_tokens) from a chat model's usage_metadata.

    LangChain exposes usage_metadata as a dict; older/other shapes expose it as
    an object. Handle both, defaulting to 0 so a missing/odd shape never crashes
    the stream — it just records zero.
    """
    if not meta:
        return 0, 0
    if isinstance(meta, dict):
        return int(meta.get("input_tokens", 0) or 0), int(meta.get("output_tokens", 0) or 0)
    return (
        int(getattr(meta, "input_tokens", 0) or 0),
        int(getattr(meta, "output_tokens", 0) or 0),
    )


# ── Sessions ──────────────────────────────────────────────────────────────────

@router.post("/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: SessionCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = service.create_session(
        db,
        user_id=user.id,
        title=body.title,
        context_type=body.context_type,
        context_ref=body.context_ref,
        account_id=body.account_id,
    )
    return session


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    limit: int = Query(50, le=100),
    cursor: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    items, next_cursor = service.list_sessions(db, user_id=user.id, limit=limit, cursor=cursor)
    return SessionListResponse(items=items, next_cursor=next_cursor)


@router.get("/sessions/by-context", response_model=SessionResponse)
async def get_context_session(
    context_type: str = Query(...),
    context_ref: str = Query(...),
    account_id: Optional[uuid.UUID] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = service.get_or_create_context_session(
        db,
        user_id=user.id,
        context_type=context_type,
        context_ref=context_ref,
        account_id=account_id,
    )
    return session


@router.get("/sessions/{session_id}", response_model=SessionWithMessagesResponse)
async def get_session(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return service.get_session(db, session_id=session_id, user_id=user.id)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    service.delete_session(db, session_id=session_id, user_id=user.id)


# ── Streaming send ────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/stream")
@limiter.limit("12/minute")
async def stream_chat(
    request: Request,
    session_id: uuid.UUID,
    body: MessageRequest,
    user: User = Depends(get_current_user),
):
    """Send a message and stream the AI response as SSE."""
    await quota_check(user.id)

    # Run sync DB prep in a thread (fast CRUD — doesn't block the loop long,
    # but wrapping keeps us consistent with the fully-async contract)
    account_ids, context_block, lg_config = await anyio.to_thread.run_sync(
        lambda: service.prepare_turn(session_id, user.id, body.content)
    )
    safe_response = internal_disclosure_response(body.content)

    async def gen():
        full_tokens: List[str] = []
        input_tokens = 0
        output_tokens = 0
        try:
            if safe_response is not None:
                msg_id = await anyio.to_thread.run_sync(
                    lambda: service.persist_assistant_turn(session_id, user.id, safe_response)
                )
                await quota_debit(user.id, credits=1)
                yield _sse({"type": "token", "v": safe_response})
                yield _sse({"type": "done", "message_id": str(msg_id)})
                return

            compiled = get_compiled()
            async for ev in compiled.astream_events(
                {"messages": [("human", body.content)]},
                config=lg_config,
                version="v2",
            ):
                kind = ev["event"]
                if kind == "on_chat_model_stream":
                    tok = ev["data"]["chunk"].content
                    if tok:
                        full_tokens.append(tok)
                        yield _sse({"type": "token", "v": tok})
                elif kind == "on_tool_start":
                    yield _sse({"type": "tool", "name": ev["name"]})
                elif kind == "on_chat_model_end":
                    usage = ev.get("data", {}).get("output", {})
                    # usage_metadata is a dict ({"input_tokens": .., "output_tokens": ..}),
                    # NOT an attribute object — getattr would always miss and leave
                    # the counts at 0. Use dict access via _read_usage().
                    meta = getattr(usage, "usage_metadata", None)
                    inp, outp = _read_usage(meta)
                    input_tokens += inp
                    output_tokens += outp

            content = "".join(full_tokens)
            msg_id = await anyio.to_thread.run_sync(
                lambda: service.persist_assistant_turn(
                    session_id, user.id, content, input_tokens, output_tokens
                )
            )
            await quota_debit(user.id, credits=max(1, output_tokens // 500 + 1),
                              input_tokens=input_tokens, output_tokens=output_tokens)
            yield _sse({"type": "done", "message_id": str(msg_id)})

        except Exception:
            logger.exception("SSE stream error session=%s user=%s", session_id, user.id)
            yield _sse({"type": "error", "detail": "Something went wrong. Please try again."})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


# ── Non-stream fallback ────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/message", response_model=MessageResponse)
@limiter.limit("12/minute")
async def send_message(
    request: Request,
    session_id: uuid.UUID,
    body: MessageRequest,
    user: User = Depends(get_current_user),
):
    """Non-streaming send (mobile / retry fallback). Blocks until LLM responds."""
    await quota_check(user.id)

    account_ids, context_block, lg_config = await anyio.to_thread.run_sync(
        lambda: service.prepare_turn(session_id, user.id, body.content)
    )
    safe_response = internal_disclosure_response(body.content)

    if safe_response is not None:
        msg_id = await anyio.to_thread.run_sync(
            lambda: service.persist_assistant_turn(session_id, user.id, safe_response)
        )
        await quota_debit(user.id, credits=1)
        from app.core.database import SessionLocal
        with SessionLocal() as db:
            session_obj = repo.get_session(db, session_id=session_id, user_id=user.id)
            for m in session_obj.messages:
                if m.id == msg_id:
                    return MessageResponse.model_validate(m)
        raise HTTPException(status_code=500, detail="Failed to retrieve assistant message.")

    compiled = get_compiled()

    result = await compiled.ainvoke(
        {"messages": [("human", body.content)]},
        config=lg_config,
    )

    # Extract last non-tool message
    response_text = "I wasn't able to generate a response."
    for msg in reversed(result["messages"]):
        if hasattr(msg, "content") and msg.content and not getattr(msg, "tool_calls", None):
            response_text = msg.content
            break

    msg_id = await anyio.to_thread.run_sync(
        lambda: service.persist_assistant_turn(session_id, user.id, response_text)
    )
    await quota_debit(user.id, credits=1)

    # Return the persisted message. Build the response schema while the session
    # is still open — returning a bare ORM object lets FastAPI serialize it after
    # the session closes, which raises DetachedInstanceError on expired attrs.
    from app.core.database import SessionLocal
    with SessionLocal() as db:
        session_obj = repo.get_session(db, session_id=session_id, user_id=user.id)
        for m in session_obj.messages:
            if m.id == msg_id:
                return MessageResponse.model_validate(m)
    raise HTTPException(status_code=500, detail="Failed to retrieve assistant message.")


# ── Insights ──────────────────────────────────────────────────────────────────

@router.get("/insights", response_model=list[InsightResponse])
async def get_insights(
    account_id: Optional[uuid.UUID] = Query(None),
    kind: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return service.get_insights(db, user_id=user.id, account_id=account_id, kind=kind)


# ── Coach's Read (day-level AI narrative) ──────────────────────────────────────

@router.get("/coach-read", response_model=CoachReadResponse)
async def coach_read(
    account_id: uuid.UUID = Query(...),
    date: str = Query(..., description="Trading day, YYYY-MM-DD"),
    refresh: bool = Query(False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Generate (or return cached) the Coach's Read for one trading day."""
    from datetime import date as date_cls

    try:
        trading_date = date_cls.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="date must be YYYY-MM-DD.")

    # Ownership: the account must belong to this user.
    owned = {a["id"] for a in repo.get_accounts_for_user(db, user_id=user.id)}
    if str(account_id) not in owned:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")

    result = await anyio.to_thread.run_sync(
        lambda: service.generate_coach_read(
            user_id=user.id,
            account_id=account_id,
            trading_date=trading_date,
            refresh=refresh,
        )
    )
    return CoachReadResponse(**result)


@router.get("/trade-review", response_model=TradeReviewResponse)
async def trade_review(
    trade_id: uuid.UUID = Query(...),
    refresh: bool = Query(False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Generate (or return cached) the AI review for one trade."""
    owned = {a["id"] for a in repo.get_accounts_for_user(db, user_id=user.id)}

    result = await anyio.to_thread.run_sync(
        lambda: service.generate_trade_review(
            user_id=user.id,
            trade_id=trade_id,
            owned_account_ids=owned,
            refresh=refresh,
        )
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trade not found.")
    return TradeReviewResponse(**result)


# ── Suggestions ───────────────────────────────────────────────────────────────

@router.get("/suggestions", response_model=SuggestedPromptsResponse)
async def get_suggestions(user: User = Depends(get_current_user)):
    # All-time framing — no "right now / recent / last month". Recency-scoped
    # prompts return nothing useful against a fixed past dataset (e.g. demo
    # data), so these ask over the full trading history instead.
    prompts = [
        "What's hurting my performance the most?",
        "Summarize my trading and identify key patterns.",
        "What's my overall profit factor?",
        "Am I revenge trading or overtrading?",
        "Which setup or symbol is making me the most money?",
    ]
    return SuggestedPromptsResponse(prompts=prompts)


# ── Usage / quota ─────────────────────────────────────────────────────────────

@router.get("/usage", response_model=UsageResponse)
async def get_usage(user: User = Depends(get_current_user)):
    data = await get_usage_response(user.id)
    return UsageResponse(**data)

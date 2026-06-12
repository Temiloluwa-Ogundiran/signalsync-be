"""
Shared repository helpers.

Usage
-----
  from app.shared.repository import get_or_404, paginate

  # in a service function:
  account = get_or_404(db, Account, account_id, detail="Account not found.")

  items, next_cursor = paginate(
      db,
      stmt=select(Post).where(Post.stream_id == stream_id).order_by(Post.created_at.desc(), Post.id.desc()),
      cursor_id=cursor_post_id,
      limit=limit,
      id_col=Post.id,
      created_col=Post.created_at,
      direction="desc",
  )
"""
import uuid
from typing import Any, TypeVar

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

T = TypeVar("T")


def get_or_404(
    db: Session,
    model: type[T],
    pk: uuid.UUID,
    *,
    detail: str = "Not found.",
    extra_filters: list | None = None,
) -> T:
    """Fetch a row by primary key or raise HTTP 404."""
    stmt = select(model).where(model.id == pk)
    if extra_filters:
        stmt = stmt.where(*extra_filters)
    obj = db.execute(stmt).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return obj


def paginate(
    db: Session,
    *,
    stmt,
    cursor_id: uuid.UUID | None,
    limit: int,
    id_col: Any,
    created_col: Any,
    direction: str = "desc",
) -> tuple[list, uuid.UUID | None]:
    """
    Keyset / cursor pagination over a pre-built select statement.

    Returns (items, next_cursor_id).
    `next_cursor_id` is None when there are no more pages.
    The caller is responsible for including `order_by` and `limit` in `stmt`
    — this helper only adds the cursor WHERE clause and over-fetches by 1
    to detect the next page.
    """
    if cursor_id is not None:
        cursor_stmt = select(created_col, id_col).where(id_col == cursor_id)
        cursor_row = db.execute(cursor_stmt).one_or_none()
        if cursor_row is not None:
            cursor_ts, cursor_pk = cursor_row
            if direction == "desc":
                stmt = stmt.where(
                    (created_col < cursor_ts) | ((created_col == cursor_ts) & (id_col < cursor_pk))
                )
            else:
                stmt = stmt.where(
                    (created_col > cursor_ts) | ((created_col == cursor_ts) & (id_col > cursor_pk))
                )

    rows = list(db.execute(stmt.limit(limit + 1)).unique().scalars())
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = items[-1].id if has_more and items else None
    return items, next_cursor

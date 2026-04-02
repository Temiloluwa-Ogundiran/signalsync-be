import uuid
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.domains.auth.models import Token, TokenType


def create(
    db: Session,
    *,
    user_id: UUID,
    hashed_token: str,
    token_type: TokenType,
    expires_at: datetime,
) -> Token:
    token = Token(
        user_id=user_id,
        token=hashed_token,
        type=token_type,
        expires_at=expires_at,
        is_revoked=False,
    )
    db.add(token)
    db.flush()
    return token


def get_active(
    db: Session,
    *,
    hashed_token: str,
    token_type: TokenType,
) -> Optional[Token]:
    return (
        db.query(Token)
        .filter(
            Token.token == hashed_token,
            Token.type == token_type,
            Token.is_revoked == False,  # noqa: E712
            Token.expires_at > datetime.now(timezone.utc),
        )
        .first()
    )


def revoke(db: Session, token: Token) -> None:
    token.is_revoked = True
    db.flush()


def revoke_all_by_user_and_type(
    db: Session,
    *,
    user_id: UUID,
    token_type: TokenType,
) -> None:
    """Revoke every active token of a given type for a user (used on logout)."""
    db.query(Token).filter(
        Token.user_id == user_id,
        Token.type == token_type,
        Token.is_revoked == False,  # noqa: E712
    ).update({"is_revoked": True}, synchronize_session="fetch")

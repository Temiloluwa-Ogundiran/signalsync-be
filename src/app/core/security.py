import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Union
from uuid import UUID

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(
    subject: Union[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> str:
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode = {"exp": expire, "sub": str(subject)}
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(subject: Union[str, Any]) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode = {"exp": expire, "sub": str(subject), "type": "refresh"}
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None


def hash_token(raw_token: str) -> str:
    """SHA-256 hash a raw token before storing it in the DB."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


def create_uuid_token(db: Session, user_id: UUID, token_type: Any) -> str:
    """
    Issue a new UUID token for the given user + type.
    Revokes any existing active token of the same type first.
    Import Token and TokenType inside callers to avoid circular imports.
    """
    from sqlalchemy import select  # noqa: PLC0415
    from app.domains.auth.models import Token, TokenType  # noqa: PLC0415

    stmt = select(Token).where(
        Token.user_id == user_id,
        Token.type == token_type,
        Token.is_revoked.is_(False),
        Token.expires_at > datetime.now(timezone.utc),
    )
    existing = db.execute(stmt).scalar_one_or_none()
    if existing:
        existing.is_revoked = True
        db.flush()

    return str(uuid.uuid4())

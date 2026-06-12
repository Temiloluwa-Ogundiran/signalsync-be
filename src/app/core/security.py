import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Union

import bcrypt
import jwt as pyjwt

from app.core.config import settings

# A dummy hash computed once at module load — used to ensure constant-time
# password verification for unknown emails (prevents timing oracle attacks).
_DUMMY_HASH = bcrypt.hashpw(b"dummy-timing-constant", bcrypt.gensalt()).decode()


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())
    except ValueError:
        return False


def create_access_token(
    subject: Union[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> str:
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode = {"exp": expire, "sub": str(subject), "typ": "access"}
    return pyjwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        return pyjwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except pyjwt.PyJWTError:
        return None


def hash_token(raw_token: str) -> str:
    """SHA-256 hash a raw token before storing it in the DB."""
    return hashlib.sha256(raw_token.encode()).hexdigest()

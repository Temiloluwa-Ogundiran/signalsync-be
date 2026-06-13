from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token
from app.domains.users.models import User
from app.domains.users import repository as user_repo

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    payload = decode_token(token)
    if payload is None:
        raise credentials_exception

    # Reject anything that isn't an access token (refresh/reset/verify tokens
    # must never authenticate a request). The "typ" claim is minted in
    # create_access_token; tokens without it are pre-claim and already expired.
    if payload.get("typ") != "access":
        raise credentials_exception

    user_id: str = payload.get("sub")
    if not user_id:
        raise credentials_exception

    user = user_repo.get_by_id(db, user_id)
    if not user or user.is_deleted or not user.is_email_verified:
        raise credentials_exception

    return user

from collections.abc import Callable

from fastapi import Depends, HTTPException, status

from app.domains.users.models import PlatformRole, User
from app.shared.deps import get_current_user


def _require_roles(*allowed: PlatformRole) -> Callable:
    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.platform_role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this area.",
            )
        return current_user

    return dependency


require_admin = _require_roles(PlatformRole.ADMIN, PlatformRole.SUPER_ADMIN)
require_technical_admin = _require_roles(
    PlatformRole.TECHNICAL_ADMIN, PlatformRole.SUPER_ADMIN
)
require_operator = _require_roles(
    PlatformRole.ADMIN,
    PlatformRole.TECHNICAL_ADMIN,
    PlatformRole.SUPER_ADMIN,
)

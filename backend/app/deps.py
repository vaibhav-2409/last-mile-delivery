from __future__ import annotations

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models import Role, User
from .security import decode_access_token

bearer = HTTPBearer(auto_error=False)

CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Sign in to continue.",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None or not creds.credentials:
        raise CREDENTIALS_ERROR
    try:
        payload = decode_access_token(creds.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Your session expired. Sign in again.")
    except jwt.PyJWTError:
        raise CREDENTIALS_ERROR

    user = db.get(User, int(payload.get("sub", 0)))
    if user is None or not user.is_active:
        raise CREDENTIALS_ERROR
    return user


def require_roles(*roles: Role):
    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"This action needs the {' or '.join(r.value for r in roles)} role.",
            )
        return user

    return _dep


require_admin = require_roles(Role.ADMIN)
require_agent = require_roles(Role.AGENT)
require_customer = require_roles(Role.CUSTOMER)
require_admin_or_agent = require_roles(Role.ADMIN, Role.AGENT)

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request, status

from .routers.auth import session_user_from_token


async def current_user(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        token = request.cookies.get("wx_session")
    user = await session_user_from_token(token) if token else None
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    return user


async def optional_user(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict | None:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        token = request.cookies.get("wx_session")
    return await session_user_from_token(token) if token else None


# Short aliases
Me = Depends(current_user)
MaybeMe = Depends(optional_user)


ROLE_RANK = {"viewer": 0, "agent": 1, "admin": 2}


def require_role(min_role: str):
    """Dependency factory. Usage: `user: dict = Depends(require_role("agent"))`.

    Roles: viewer < agent < admin. `viewer` can read but not mutate; `agent`
    can mutate tickets / conversations / teams; `admin` can mutate workspace
    settings and integrations.
    """
    min_rank = ROLE_RANK.get(min_role, 1)

    async def checker(user: dict = Me) -> dict:
        user_rank = ROLE_RANK.get(user.get("role") or "viewer", 0)
        if user_rank < min_rank:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role '{user.get('role')}' cannot perform this action (needs {min_role})",
            )
        return user

    return Depends(checker)


AgentOrAdmin = require_role("agent")
AdminOnly = require_role("admin")

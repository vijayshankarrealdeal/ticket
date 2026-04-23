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

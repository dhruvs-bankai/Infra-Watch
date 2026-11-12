from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User


bearer = HTTPBearer(auto_error=False)


def get_session(request: Request):
    factory = request.app.state.session_factory
    with factory() as session:
        yield session


def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: Annotated[Session, Depends(get_session)],
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    settings = request.app.state.settings
    try:
        claims = jwt.decode(
            credentials.credentials,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["sub", "exp", "iat", "nbf", "iss", "aud", "ver", "typ"]},
        )
        if claims.get("typ") != "access":
            raise unauthorized
        user_id = UUID(claims["sub"])
        user = session.scalar(select(User).where(User.id == user_id))
        if user is None or not user.is_admin or claims.get("ver") != user.token_version:
            raise unauthorized
        return user
    except (jwt.InvalidTokenError, ValueError, KeyError):
        raise unauthorized from None


CurrentUser = Annotated[User, Depends(get_current_user)]

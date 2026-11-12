import hmac
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentUser, get_session
from app.core.security import create_access_token, hash_password, verify_password
from app.models import AppSetting, User
from app.schemas.auth import LoginRequest, MessageResponse, SetupRequest, TokenResponse, UserResponse
from app.services.audit import record_audit


router = APIRouter(prefix="/auth", tags=["authentication"])
SessionDep = Annotated[Session, Depends(get_session)]


def _token_response(request: Request, user: User) -> TokenResponse:
    settings = request.app.state.settings
    token, expires_at = create_access_token(
        user_id=user.id,
        token_version=user.token_version,
        secret=settings.jwt_secret_key.get_secret_value(),
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        lifetime_minutes=settings.jwt_access_token_minutes,
    )
    seconds = max(0, int((expires_at - datetime.now(timezone.utc)).total_seconds()))
    return TokenResponse(access_token=token, expires_in=seconds, user=UserResponse.model_validate(user))


@router.post("/setup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def setup_admin(payload: SetupRequest, request: Request, session: SessionDep) -> TokenResponse:
    settings = request.app.state.settings
    if not hmac.compare_digest(payload.setup_token, settings.initial_admin_setup_token.get_secret_value()):
        raise HTTPException(status_code=403, detail="Setup token is invalid")

    setup_state = session.scalar(select(AppSetting).where(AppSetting.key == "initial_admin_setup_complete").with_for_update())
    if setup_state is None:
        raise HTTPException(status_code=503, detail="Database is not migrated")
    if setup_state.value.get("complete") or session.scalar(select(func.count()).select_from(User)):
        raise HTTPException(status_code=409, detail="Initial administrator has already been configured")

    user = User(username=payload.username, password_hash=hash_password(payload.password), is_admin=True)
    session.add(user)
    setup_state.value = {"complete": True}
    record_audit(
        session,
        actor_id=None,
        action="auth.initial_admin_created",
        resource_type="user",
        resource_id=payload.username,
        request_id=request.state.request_id,
    )
    session.commit()
    session.refresh(user)
    return _token_response(request, user)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, session: SessionDep) -> TokenResponse:
    user = session.scalar(select(User).where(User.username == payload.username))
    if user is None or not user.is_admin or not verify_password(payload.password, user.password_hash if user else None):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _token_response(request, user)


@router.get("/me", response_model=UserResponse)
def current_user(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)


@router.post("/logout", response_model=MessageResponse)
def logout(user: CurrentUser, request: Request, session: SessionDep) -> MessageResponse:
    user.token_version += 1
    record_audit(
        session,
        actor_id=user.id,
        action="auth.logout",
        resource_type="user",
        resource_id=str(user.id),
        request_id=request.state.request_id,
    )
    session.commit()
    return MessageResponse(message="Session invalidated")

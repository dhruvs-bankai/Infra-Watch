from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from pwdlib import PasswordHash


password_hash = PasswordHash.recommended()
_dummy_hash = password_hash.hash("infra-watch-invalid-user-password")


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, encoded: str | None) -> bool:
    return password_hash.verify(password, encoded or _dummy_hash) and encoded is not None


def create_access_token(
    user_id: UUID,
    token_version: int,
    secret: str,
    issuer: str,
    audience: str,
    lifetime_minutes: int,
) -> tuple[str, datetime]:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=lifetime_minutes)
    payload = {
        "sub": str(user_id),
        "ver": token_version,
        "iat": now,
        "nbf": now,
        "exp": expires_at,
        "iss": issuer,
        "aud": audience,
        "typ": "access",
    }
    token = jwt.encode(payload, secret, algorithm="HS256")
    return token, expires_at


def decode_access_token(token: str, secret: str, issuer: str, audience: str) -> dict:
    return jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        issuer=issuer,
        audience=audience,
        options={"require": ["sub", "exp", "iat", "nbf", "iss", "aud", "ver", "typ"]},
    )

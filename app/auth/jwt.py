"""
JWT token creation and validation.

We use two token types:
- Access tokens: Short-lived (30 min), used for API requests
- Refresh tokens: Long-lived (7 days), used only to get new access tokens

This dual-token approach limits damage from token theft while maintaining
good UX (users don't need to re-login frequently).

See README.md in this directory for detailed security explanations.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
import secrets

from jose import jwt, JWTError

from app.config import Settings


def create_access_token(user_id: str, settings: Settings) -> str:
    """
    Create a short-lived access token for API authentication.

    Args:
        user_id: The user's database ID
        settings: Application settings containing JWT configuration

    Returns:
        Encoded JWT string
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.jwt_access_token_expire_minutes
    )
    payload = {
        "sub": user_id,  # Subject: who this token is for
        "exp": expire,  # Expiration time
        "type": "access",  # Token type for validation
        "iat": datetime.now(timezone.utc),  # Issued at
        "jti": secrets.token_urlsafe(16),  # Unique ID for potential revocation
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: str, settings: Settings) -> str:
    """
    Create a long-lived refresh token for obtaining new access tokens.

    Refresh tokens should be:
    - Stored securely (localStorage on frontend)
    - Rotated on each use (old token invalidated)
    - Only sent to the /auth/refresh endpoint

    Args:
        user_id: The user's database ID
        settings: Application settings containing JWT configuration

    Returns:
        Encoded JWT string
    """
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.jwt_refresh_token_expire_days
    )
    payload = {
        "sub": user_id,
        "exp": expire,
        "type": "refresh",
        "iat": datetime.now(timezone.utc),
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str, settings: Settings) -> Optional[dict]:
    """
    Decode and validate a JWT token.

    Validates:
    - Signature (token wasn't tampered with)
    - Expiration (token hasn't expired)
    - Algorithm (matches expected algorithm)

    Args:
        token: The JWT string to decode
        settings: Application settings containing JWT configuration

    Returns:
        Decoded payload dict if valid, None if invalid/expired
    """
    try:
        return jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError:
        return None

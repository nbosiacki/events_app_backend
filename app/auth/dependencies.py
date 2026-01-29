"""
FastAPI dependencies for authentication.

These are injected into route handlers to:
- Require authentication (get_current_user)
- Optionally get user if authenticated (get_current_user_optional)

Usage in routes:
    @router.get("/protected")
    async def protected_route(user: dict = Depends(get_current_user)):
        return {"user_id": user["_id"]}
"""

from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from bson import ObjectId

from app.auth.jwt import decode_token
from app.config import get_settings
from app.db.mongodb import get_database

# OAuth2PasswordBearer extracts the token from the Authorization header
# tokenUrl is the endpoint where tokens are obtained (for OpenAPI docs)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# Same but doesn't raise error if token is missing
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """
    Dependency that requires a valid access token.

    Extracts the JWT from the Authorization header, validates it,
    and returns the user document from the database.

    Raises:
        HTTPException 401: If token is missing, invalid, or expired
        HTTPException 401: If user no longer exists
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    settings = get_settings()
    payload = decode_token(token, settings)

    if payload is None:
        raise credentials_exception

    # Verify this is an access token, not a refresh token
    if payload.get("type") != "access":
        raise credentials_exception

    user_id = payload.get("sub")
    if user_id is None:
        raise credentials_exception

    # Fetch user from database
    db = get_database()
    try:
        user = await db.users.find_one({"_id": ObjectId(user_id)})
    except Exception:
        raise credentials_exception

    if user is None:
        raise credentials_exception

    # Check if account is locked
    if user.get("locked_until"):
        from datetime import datetime, timezone
        if datetime.now(timezone.utc) < user["locked_until"]:
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail="Account is temporarily locked",
            )

    # Convert ObjectId to string for JSON serialization
    user["_id"] = str(user["_id"])
    return user


async def get_current_user_optional(
    token: Optional[str] = Depends(oauth2_scheme_optional),
) -> Optional[dict]:
    """
    Dependency that optionally gets the current user.

    If a valid token is provided, returns the user.
    If no token or invalid token, returns None instead of raising.

    Useful for routes that behave differently for authenticated users
    but don't require authentication.
    """
    if token is None:
        return None

    try:
        return await get_current_user(token)
    except HTTPException:
        return None

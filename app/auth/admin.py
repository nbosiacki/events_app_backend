"""Admin API key authentication dependency."""

from fastapi import Header, HTTPException, status

from app.config import get_settings


async def require_admin_key(x_admin_key: str = Header(...)):
    """Dependency that requires a valid admin API key in X-Admin-Key header.

    Raises 401 if the key doesn't match ADMIN_API_KEY.
    Raises 403 if ADMIN_API_KEY is not configured (admin disabled).
    """
    settings = get_settings()

    if not settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access is not configured",
        )

    if x_admin_key != settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin API key",
        )

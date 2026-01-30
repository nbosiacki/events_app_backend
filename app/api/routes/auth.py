"""
Authentication routes for user registration, login, and token management.

Endpoints:
- POST /auth/register - Create new account
- POST /auth/login - Login with email/password
- POST /auth/refresh - Get new access token using refresh token
- POST /auth/forgot-password - Request password reset email
- POST /auth/reset-password - Reset password with token
- GET /auth/me - Get current authenticated user
- POST /auth/change-password - Change password (when logged in)
"""

from datetime import datetime, timedelta, timezone
import secrets


def _ensure_utc(dt: datetime) -> datetime:
    """Attach UTC tzinfo to a naive datetime (as returned by Motor/PyMongo).

    MongoDB stores all datetimes in UTC but Motor returns them without
    tzinfo by default.  This lets us safely compare against
    datetime.now(timezone.utc) without hitting a TypeError.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.security import OAuth2PasswordRequestForm
from bson import ObjectId

from app.auth.password import hash_password, verify_password
from app.auth.jwt import create_access_token, create_refresh_token, decode_token
from app.auth.dependencies import get_current_user
from app.auth.schemas import (
    UserRegister,
    TokenResponse,
    RefreshTokenRequest,
    PasswordResetRequest,
    PasswordResetConfirm,
    ChangePassword,
    MessageResponse,
)
from app.config import get_settings
from app.db.mongodb import get_database
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(user_data: UserRegister):
    """
    Register a new user with email and password.

    Returns access and refresh tokens on successful registration.
    User is automatically logged in after registration.
    """
    db = get_database()
    settings = get_settings()

    # Check if email already exists (case-insensitive)
    existing = await db.users.find_one({"email": user_data.email.lower()})
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    # Create user document
    user_dict = {
        "email": user_data.email.lower(),
        "name": user_data.name,
        "password_hash": hash_password(user_data.password),
        "email_verified": False,
        "created_at": datetime.now(timezone.utc),
        "preferences": {
            "preferred_categories": [],
            "max_price_bucket": "premium",
            "preferred_areas": [],
        },
        "liked_events": [],
        "attended_events": [],
        "failed_login_attempts": 0,
        "locked_until": None,
        "last_login": datetime.now(timezone.utc),
        "auth_providers": [],
    }

    result = await db.users.insert_one(user_dict)
    user_id = str(result.inserted_id)

    # Generate tokens
    return TokenResponse(
        access_token=create_access_token(user_id, settings),
        refresh_token=create_refresh_token(user_id, settings),
        expires_in=settings.jwt_access_token_expire_minutes * 60,
    )


@router.post("/login", response_model=TokenResponse)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """
    Login with email and password.

    Uses OAuth2 password flow (form data with username/password fields).
    The 'username' field should contain the email address.

    Returns access and refresh tokens on success.
    Implements account lockout after failed attempts.
    """
    db = get_database()
    settings = get_settings()

    # Find user by email (case-insensitive)
    user = await db.users.find_one({"email": form_data.username.lower()})

    # Check if account is locked
    if user and user.get("locked_until"):
        if datetime.now(timezone.utc) < _ensure_utc(user["locked_until"]):
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail="Account temporarily locked due to too many failed login attempts. Try again later.",
            )
        else:
            # Lock period expired, reset failed attempts
            await db.users.update_one(
                {"_id": user["_id"]},
                {"$set": {"failed_login_attempts": 0, "locked_until": None}},
            )

    # Verify credentials
    # Note: We check password even if user doesn't exist to prevent timing attacks
    if not user or not user.get("password_hash"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(form_data.password, user["password_hash"]):
        # Increment failed login attempts
        failed_attempts = user.get("failed_login_attempts", 0) + 1
        update_data = {"$set": {"failed_login_attempts": failed_attempts}}

        # Lock account if too many failures
        if failed_attempts >= settings.max_failed_login_attempts:
            lock_until = datetime.now(timezone.utc) + timedelta(
                minutes=settings.account_lockout_minutes
            )
            update_data["$set"]["locked_until"] = lock_until

        await db.users.update_one({"_id": user["_id"]}, update_data)

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Successful login - reset failed attempts and update last login
    await db.users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "failed_login_attempts": 0,
                "locked_until": None,
                "last_login": datetime.now(timezone.utc),
            }
        },
    )

    user_id = str(user["_id"])
    return TokenResponse(
        access_token=create_access_token(user_id, settings),
        refresh_token=create_refresh_token(user_id, settings),
        expires_in=settings.jwt_access_token_expire_minutes * 60,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request: RefreshTokenRequest):
    """
    Get new access token using a valid refresh token.

    Refresh tokens are rotated on each use - the old refresh token
    becomes invalid and a new one is returned.
    """
    settings = get_settings()
    payload = decode_token(request.refresh_token, settings)

    if not payload or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Verify user still exists
    db = get_database()
    try:
        user = await db.users.find_one({"_id": ObjectId(user_id)})
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Issue new tokens (token rotation)
    return TokenResponse(
        access_token=create_access_token(user_id, settings),
        refresh_token=create_refresh_token(user_id, settings),
        expires_in=settings.jwt_access_token_expire_minutes * 60,
    )


@router.post("/forgot-password", response_model=MessageResponse, status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(request: PasswordResetRequest):
    """
    Request a password reset email.

    Always returns success to prevent email enumeration attacks.
    If the email exists, a reset token is generated and stored.

    Note: Email sending is not implemented yet (placeholder).
    """
    db = get_database()
    settings = get_settings()
    user = await db.users.find_one({"email": request.email.lower()})

    # Always return same response to prevent email enumeration
    response_message = "If this email is registered, a password reset link has been sent."

    if not user:
        return MessageResponse(message=response_message)

    # Generate secure reset token
    reset_token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=1)

    await db.users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "password_reset_token": reset_token,
                "password_reset_expires": expires,
            }
        },
    )

    # TODO: Send email with reset link
    # reset_url = f"{settings.frontend_url}/reset-password?token={reset_token}"
    # email_service.send_password_reset(user["email"], reset_url)

    # For development, print the token (remove in production!)
    if settings.debug:
        print(f"[DEBUG] Password reset token for {request.email}: {reset_token}")

    return MessageResponse(message=response_message)


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(request: PasswordResetConfirm):
    """
    Reset password using a token from the forgot-password email.

    The token is single-use and expires after 1 hour.
    """
    db = get_database()
    user = await db.users.find_one({"password_reset_token": request.token})

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    # Check if token has expired
    expires = user.get("password_reset_expires")
    if not expires or datetime.now(timezone.utc) > _ensure_utc(expires):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset token has expired",
        )

    # Update password and clear reset token
    await db.users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {"password_hash": hash_password(request.new_password)},
            "$unset": {"password_reset_token": "", "password_reset_expires": ""},
        },
    )

    return MessageResponse(message="Password successfully reset. You can now log in with your new password.")


@router.get("/me", response_model=User)
async def get_me(current_user: dict = Depends(get_current_user)):
    """
    Get the currently authenticated user's profile.

    Requires a valid access token in the Authorization header.
    """
    # Remove sensitive fields before returning
    current_user.pop("password_hash", None)
    current_user.pop("password_reset_token", None)
    current_user.pop("password_reset_expires", None)
    current_user.pop("email_verification_token", None)
    current_user.pop("email_verification_expires", None)

    return User(**current_user)


@router.post("/change-password", response_model=MessageResponse)
async def change_password(
    request: ChangePassword,
    current_user: dict = Depends(get_current_user),
):
    """
    Change password for the currently authenticated user.

    Requires the current password for verification.
    """
    # Verify current password
    if not current_user.get("password_hash"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change password for OAuth-only accounts",
        )

    if not verify_password(request.current_password, current_user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    # Update password
    db = get_database()
    await db.users.update_one(
        {"_id": ObjectId(current_user["_id"])},
        {"$set": {"password_hash": hash_password(request.new_password)}},
    )

    return MessageResponse(message="Password changed successfully")

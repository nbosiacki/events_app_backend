from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List
from datetime import datetime, timezone


class UserPreferences(BaseModel):
    """User's event preferences for personalized recommendations."""

    preferred_categories: List[str] = Field(default_factory=list)
    max_price_bucket: str = "premium"
    preferred_areas: List[str] = Field(default_factory=list)


class AuthProvider(BaseModel):
    """
    Linked OAuth provider for federated login (future feature).

    This schema supports future integration with Google, Apple, Meta, etc.
    """

    provider: str  # "google", "apple", "meta", etc.
    provider_user_id: str  # User's ID from the OAuth provider
    linked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UserBase(BaseModel):
    """Base user fields shared across schemas."""

    email: EmailStr
    name: Optional[str] = None
    preferences: UserPreferences = Field(default_factory=UserPreferences)


class UserCreate(UserBase):
    """Schema for creating a user (used by auth registration)."""

    pass


class User(UserBase):
    """Full user schema with all fields."""

    id: Optional[str] = Field(default=None, alias="_id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    liked_events: List[str] = Field(default_factory=list)
    attended_events: List[str] = Field(default_factory=list)

    # Authentication fields
    password_hash: Optional[str] = None  # Nullable for future OAuth-only users
    email_verified: bool = False
    email_verification_token: Optional[str] = None
    email_verification_expires: Optional[datetime] = None
    password_reset_token: Optional[str] = None
    password_reset_expires: Optional[datetime] = None

    # Security fields
    failed_login_attempts: int = 0
    locked_until: Optional[datetime] = None
    last_login: Optional[datetime] = None

    # Federated login (future feature)
    auth_providers: List[AuthProvider] = Field(default_factory=list)

    class Config:
        populate_by_name = True


class UserInDB(User):
    """
    User as stored in database.

    Includes password_hash which should never be returned to clients.
    """

    password_hash: Optional[str] = None

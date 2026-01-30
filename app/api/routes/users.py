"""
User management routes.

All routes require authentication and verify the user is modifying their own data.
User registration is handled by /auth/register.
"""

from fastapi import APIRouter, HTTPException, Depends, status
from bson import ObjectId

from app.db.mongodb import get_database
from app.models.user import User, UserPreferences
from app.auth.dependencies import get_current_user

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/{user_id}", response_model=User)
async def get_user(user_id: str, current_user: dict = Depends(get_current_user)):
    """
    Get a user by ID.

    Requires authentication. Users can only view their own profile.
    """
    db = get_database()

    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    # Users can only view their own profile
    if current_user["_id"] != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot view another user's profile",
        )

    user = await db.users.find_one({"_id": ObjectId(user_id)})

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user["_id"] = str(user["_id"])

    # Remove sensitive fields
    user.pop("password_hash", None)
    user.pop("password_reset_token", None)
    user.pop("password_reset_expires", None)
    user.pop("email_verification_token", None)
    user.pop("email_verification_expires", None)

    return User(**user)


@router.put("/{user_id}/preferences", response_model=User)
async def update_preferences(
    user_id: str,
    preferences: UserPreferences,
    current_user: dict = Depends(get_current_user),
):
    """
    Update user preferences.

    Requires authentication. Users can only update their own preferences.
    """
    db = get_database()

    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    # Verify user is updating their own preferences
    if current_user["_id"] != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot modify another user's preferences",
        )

    result = await db.users.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"preferences": preferences.model_dump()}},
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")

    user = await db.users.find_one({"_id": ObjectId(user_id)})
    user["_id"] = str(user["_id"])

    # Remove sensitive fields
    user.pop("password_hash", None)
    user.pop("password_reset_token", None)
    user.pop("password_reset_expires", None)

    return User(**user)


@router.post("/{user_id}/like/{event_id}")
async def like_event(
    user_id: str,
    event_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Mark an event as liked.

    Requires authentication. Users can only like events for their own profile.
    """
    db = get_database()

    if not ObjectId.is_valid(user_id) or not ObjectId.is_valid(event_id):
        raise HTTPException(status_code=400, detail="Invalid ID format")

    # Verify user is modifying their own data
    if current_user["_id"] != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot modify another user's liked events",
        )

    # Verify event exists
    event = await db.events.find_one({"_id": ObjectId(event_id)})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    result = await db.users.update_one(
        {"_id": ObjectId(user_id)},
        {"$addToSet": {"liked_events": event_id}},
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")

    return {"message": "Event liked successfully"}


@router.delete("/{user_id}/like/{event_id}")
async def unlike_event(
    user_id: str,
    event_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Remove a like from an event.

    Requires authentication. Users can only unlike events for their own profile.
    Idempotent — removing a non-existent like is a no-op.
    """
    db = get_database()

    if not ObjectId.is_valid(user_id) or not ObjectId.is_valid(event_id):
        raise HTTPException(status_code=400, detail="Invalid ID format")

    # Verify user is modifying their own data
    if current_user["_id"] != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot modify another user's liked events",
        )

    result = await db.users.update_one(
        {"_id": ObjectId(user_id)},
        {"$pull": {"liked_events": event_id}},
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")

    return {"message": "Event unliked successfully"}


@router.post("/{user_id}/attend/{event_id}")
async def attend_event(
    user_id: str,
    event_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Mark an event as attended.

    Requires authentication. Users can only mark attendance for their own profile.
    """
    db = get_database()

    if not ObjectId.is_valid(user_id) or not ObjectId.is_valid(event_id):
        raise HTTPException(status_code=400, detail="Invalid ID format")

    # Verify user is modifying their own data
    if current_user["_id"] != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot modify another user's attendance",
        )

    result = await db.users.update_one(
        {"_id": ObjectId(user_id)},
        {"$addToSet": {"attended_events": event_id}},
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")

    return {"message": "Event marked as attended"}

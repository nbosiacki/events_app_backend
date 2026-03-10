"""
Tests for the /api/auth endpoints.

Covers:
    POST /auth/register        – account creation, duplicate email, weak password
    POST /auth/login           – credential validation, lockout after failures
    POST /auth/refresh         – token rotation, invalid/access token rejection
    POST /auth/forgot-password – reset token generation, email enumeration prevention
    POST /auth/reset-password  – token-based reset, expired/invalid token handling
    GET  /auth/me              – authenticated profile, sensitive field stripping
    POST /auth/change-password – password update, wrong-current-password rejection

All tests hit a real MongoDB test database via the setup_db fixture.
Login uses OAuth2 form data (username + password fields).
"""

from datetime import datetime, timedelta, timezone

from app.auth.jwt import create_refresh_token, create_access_token
from app.config import get_settings

settings = get_settings()


class TestRegister:
    """POST /api/auth/register — new user creation."""

    async def test_register_success(self, client):
        """A valid registration should return 201 with access and refresh tokens."""
        from app.db import mongodb
        await mongodb.db.invite_codes.insert_one({"code": "TESTCODE", "used": False})

        response = await client.post("/api/auth/register", json={
            "email": "new@example.com",
            "password": "StrongPass1",
            "name": "New User",
            "invite_code": "TESTCODE",
        })
        assert response.status_code == 201

        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] == settings.jwt_access_token_expire_minutes * 60

    async def test_duplicate_email_returns_409(self, client, test_user):
        """Registering with an already-used email should return 409 Conflict."""
        from app.db import mongodb
        await mongodb.db.invite_codes.insert_one({"code": "DUPECODE", "used": False})

        response = await client.post("/api/auth/register", json={
            "email": "test@example.com",
            "password": "StrongPass1",
            "invite_code": "DUPECODE",
        })
        assert response.status_code == 409

    async def test_case_insensitive_email_duplicate(self, client, test_user):
        """Email uniqueness check should be case-insensitive."""
        from app.db import mongodb
        await mongodb.db.invite_codes.insert_one({"code": "CASECODE", "used": False})

        response = await client.post("/api/auth/register", json={
            "email": "TEST@example.com",
            "password": "StrongPass1",
            "invite_code": "CASECODE",
        })
        assert response.status_code == 409

    async def test_weak_password_rejected(self, client):
        """A password without uppercase/lowercase/digit should be rejected (422)."""
        response = await client.post("/api/auth/register", json={
            "email": "weak@example.com",
            "password": "alllowercase",
            "invite_code": "ANYCODE",
        })
        assert response.status_code == 422

    async def test_register_valid_invite_code(self, client):
        """Registration with a valid unused invite code should succeed and mark the code used."""
        from app.db import mongodb
        await mongodb.db.invite_codes.insert_one({"code": "VALIDINV", "used": False})

        response = await client.post("/api/auth/register", json={
            "email": "invited@example.com",
            "password": "StrongPass1",
            "name": "Invited User",
            "invite_code": "VALIDINV",
        })
        assert response.status_code == 201

        # Verify invite code is now marked as used
        code_doc = await mongodb.db.invite_codes.find_one({"code": "VALIDINV"})
        assert code_doc["used"] is True
        assert code_doc["used_by_email"] == "invited@example.com"
        assert code_doc["used_at"] is not None

    async def test_register_invalid_invite_code(self, client):
        """Registration with a non-existent invite code should return 403."""
        response = await client.post("/api/auth/register", json={
            "email": "someone@example.com",
            "password": "StrongPass1",
            "invite_code": "NOSUCHCD",
        })
        assert response.status_code == 403
        assert "invite code" in response.json()["detail"].lower()

    async def test_register_already_used_invite_code(self, client):
        """Registration with an already-used invite code should return 403."""
        from app.db import mongodb
        await mongodb.db.invite_codes.insert_one({
            "code": "USEDCODE",
            "used": True,
            "used_by_email": "previous@example.com",
        })

        response = await client.post("/api/auth/register", json={
            "email": "newcomer@example.com",
            "password": "StrongPass1",
            "invite_code": "USEDCODE",
        })
        assert response.status_code == 403
        assert "invite code" in response.json()["detail"].lower()


class TestLogin:
    """POST /api/auth/login — OAuth2 password flow."""

    async def test_login_success(self, client, test_user):
        """Valid credentials should return tokens."""
        response = await client.post("/api/auth/login", data={
            "username": "test@example.com",
            "password": "TestPass1",
        })
        assert response.status_code == 200

        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data

    async def test_wrong_password(self, client, test_user):
        """Wrong password should return 401."""
        response = await client.post("/api/auth/login", data={
            "username": "test@example.com",
            "password": "WrongPass1",
        })
        assert response.status_code == 401

    async def test_nonexistent_email(self, client):
        """An unregistered email should return 401 (same as wrong password)."""
        response = await client.post("/api/auth/login", data={
            "username": "nobody@example.com",
            "password": "AnyPass1",
        })
        assert response.status_code == 401

    async def test_case_insensitive_login(self, client, test_user):
        """Login should work regardless of email casing."""
        response = await client.post("/api/auth/login", data={
            "username": "TEST@example.com",
            "password": "TestPass1",
        })
        assert response.status_code == 200

    async def test_lockout_after_failed_attempts(self, client, test_user):
        """After max_failed_login_attempts (5) wrong passwords, account should lock (423)."""
        for _ in range(settings.max_failed_login_attempts):
            await client.post("/api/auth/login", data={
                "username": "test@example.com",
                "password": "WrongPass1",
            })

        response = await client.post("/api/auth/login", data={
            "username": "test@example.com",
            "password": "TestPass1",
        })
        assert response.status_code == 423

    async def test_successful_login_resets_failure_count(self, client, test_user):
        """A successful login should reset the failed attempt counter to 0."""
        # Fail a few times (but not enough to lock)
        for _ in range(3):
            await client.post("/api/auth/login", data={
                "username": "test@example.com",
                "password": "WrongPass1",
            })

        # Succeed
        response = await client.post("/api/auth/login", data={
            "username": "test@example.com",
            "password": "TestPass1",
        })
        assert response.status_code == 200

        # Verify counter was reset by failing again without hitting lockout
        for _ in range(3):
            await client.post("/api/auth/login", data={
                "username": "test@example.com",
                "password": "WrongPass1",
            })

        response = await client.post("/api/auth/login", data={
            "username": "test@example.com",
            "password": "TestPass1",
        })
        assert response.status_code == 200


class TestRefreshToken:
    """POST /api/auth/refresh — token rotation."""

    async def test_refresh_success(self, client, test_user):
        """A valid refresh token should return a new token pair."""
        refresh = create_refresh_token(str(test_user["_id"]), settings)
        response = await client.post("/api/auth/refresh", json={
            "refresh_token": refresh,
        })
        assert response.status_code == 200

        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data

    async def test_token_rotation_returns_new_refresh(self, client, test_user):
        """The new refresh token should be different from the one submitted."""
        refresh = create_refresh_token(str(test_user["_id"]), settings)
        response = await client.post("/api/auth/refresh", json={
            "refresh_token": refresh,
        })
        new_refresh = response.json()["refresh_token"]
        assert new_refresh != refresh

    async def test_invalid_refresh_token(self, client):
        """A garbage token string should return 401."""
        response = await client.post("/api/auth/refresh", json={
            "refresh_token": "invalid.token.string",
        })
        assert response.status_code == 401

    async def test_access_token_rejected_for_refresh(self, client, test_user):
        """An access token (type=access) must not be accepted as a refresh token."""
        access = create_access_token(str(test_user["_id"]), settings)
        response = await client.post("/api/auth/refresh", json={
            "refresh_token": access,
        })
        assert response.status_code == 401

    async def test_deleted_user_refresh_fails(self, client, test_user):
        """If the user is deleted after the refresh token was issued, refresh should fail."""
        from app.db import mongodb

        refresh = create_refresh_token(str(test_user["_id"]), settings)
        await mongodb.db.users.delete_one({"_id": test_user["_id"]})

        response = await client.post("/api/auth/refresh", json={
            "refresh_token": refresh,
        })
        assert response.status_code == 401


class TestForgotPassword:
    """POST /api/auth/forgot-password — password reset initiation."""

    async def test_existing_email_returns_202(self, client, test_user):
        """A registered email should return 202 (accepted) and store a reset token."""
        response = await client.post("/api/auth/forgot-password", json={
            "email": "test@example.com",
        })
        assert response.status_code == 202

    async def test_unknown_email_returns_same_202(self, client):
        """An unregistered email should return 202 to prevent email enumeration."""
        response = await client.post("/api/auth/forgot-password", json={
            "email": "unknown@example.com",
        })
        assert response.status_code == 202

    async def test_stores_reset_token_in_db(self, client, test_user):
        """After requesting a reset, the user document should have a reset token."""
        from app.db import mongodb

        await client.post("/api/auth/forgot-password", json={
            "email": "test@example.com",
        })
        user = await mongodb.db.users.find_one({"_id": test_user["_id"]})
        assert user["password_reset_token"] is not None
        assert user["password_reset_expires"] is not None


class TestResetPassword:
    """POST /api/auth/reset-password — token-based password reset."""

    async def test_reset_success(self, client, test_user):
        """A valid, non-expired token with a strong password should reset successfully."""
        from app.db import mongodb

        token = "valid-reset-token-123"
        await mongodb.db.users.update_one(
            {"_id": test_user["_id"]},
            {"$set": {
                "password_reset_token": token,
                "password_reset_expires": datetime.now(timezone.utc) + timedelta(hours=1),
            }},
        )

        response = await client.post("/api/auth/reset-password", json={
            "token": token,
            "new_password": "NewSecure1",
        })
        assert response.status_code == 200

        # Verify login works with new password
        login = await client.post("/api/auth/login", data={
            "username": "test@example.com",
            "password": "NewSecure1",
        })
        assert login.status_code == 200

    async def test_invalid_token(self, client):
        """A token that doesn't match any user should return 400."""
        response = await client.post("/api/auth/reset-password", json={
            "token": "nonexistent-token",
            "new_password": "NewSecure1",
        })
        assert response.status_code == 400

    async def test_expired_token(self, client, test_user):
        """An expired reset token should return 400."""
        from app.db import mongodb

        token = "expired-token-123"
        await mongodb.db.users.update_one(
            {"_id": test_user["_id"]},
            {"$set": {
                "password_reset_token": token,
                "password_reset_expires": datetime.now(timezone.utc) - timedelta(hours=1),
            }},
        )

        response = await client.post("/api/auth/reset-password", json={
            "token": token,
            "new_password": "NewSecure1",
        })
        assert response.status_code == 400

    async def test_single_use_token(self, client, test_user):
        """After a successful reset, the same token should no longer work."""
        from app.db import mongodb

        token = "single-use-token-123"
        await mongodb.db.users.update_one(
            {"_id": test_user["_id"]},
            {"$set": {
                "password_reset_token": token,
                "password_reset_expires": datetime.now(timezone.utc) + timedelta(hours=1),
            }},
        )

        # First reset succeeds
        await client.post("/api/auth/reset-password", json={
            "token": token,
            "new_password": "NewSecure1",
        })

        # Second attempt with same token fails
        response = await client.post("/api/auth/reset-password", json={
            "token": token,
            "new_password": "AnotherPass1",
        })
        assert response.status_code == 400


class TestGetMe:
    """GET /api/auth/me — authenticated user profile."""

    async def test_authenticated(self, client, auth_headers):
        """A valid access token should return the user's profile."""
        response = await client.get("/api/auth/me", headers=auth_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["email"] == "test@example.com"
        assert data["name"] == "Test User"

    async def test_unauthenticated(self, client):
        """No token should return 401."""
        response = await client.get("/api/auth/me")
        assert response.status_code == 401

    async def test_sensitive_fields_stripped(self, client, auth_headers):
        """Sensitive fields should be None in the response.

        The User model declares password_hash and reset/verification token
        fields with Optional defaults, so they appear in the JSON as null
        after the route pops them from the source dict.  The important
        thing is that the actual secret values are never exposed.
        """
        response = await client.get("/api/auth/me", headers=auth_headers)
        data = response.json()

        assert data.get("password_hash") is None
        assert data.get("password_reset_token") is None
        assert data.get("password_reset_expires") is None
        assert data.get("email_verification_token") is None
        assert data.get("email_verification_expires") is None


class TestChangePassword:
    """POST /api/auth/change-password — password update when logged in."""

    async def test_change_password_success(self, client, auth_headers):
        """Providing the correct current password and a valid new one should succeed."""
        response = await client.post("/api/auth/change-password",
            headers=auth_headers,
            json={
                "current_password": "TestPass1",
                "new_password": "NewPass123",
            },
        )
        assert response.status_code == 200

        # Verify login with new password
        login = await client.post("/api/auth/login", data={
            "username": "test@example.com",
            "password": "NewPass123",
        })
        assert login.status_code == 200

    async def test_wrong_current_password(self, client, auth_headers):
        """An incorrect current password should return 400."""
        response = await client.post("/api/auth/change-password",
            headers=auth_headers,
            json={
                "current_password": "WrongCurrent1",
                "new_password": "NewPass123",
            },
        )
        assert response.status_code == 400

    async def test_unauthenticated(self, client):
        """Change-password without a token should return 401."""
        response = await client.post("/api/auth/change-password", json={
            "current_password": "TestPass1",
            "new_password": "NewPass123",
        })
        assert response.status_code == 401

"""
Tests for low-level auth utilities: password hashing (Argon2id) and JWT
token creation / decoding.

These are pure unit tests — no database needed. The Settings object is
constructed directly rather than loaded from .env so tests are fully
self-contained.
"""

from datetime import datetime, timedelta, timezone

from jose import jwt as jose_jwt

from app.auth.password import hash_password, verify_password
from app.auth.jwt import create_access_token, create_refresh_token, decode_token
from app.config import Settings


def _make_settings(**overrides) -> Settings:
    """Build a Settings instance with sensible test defaults.

    Accepts keyword overrides so individual tests can tweak one field
    without repeating the full constructor.
    """
    defaults = {
        "app_env": "test",
        "jwt_secret_key": "test-secret",
        "jwt_algorithm": "HS256",
        "jwt_access_token_expire_minutes": 30,
        "jwt_refresh_token_expire_days": 7,
    }
    defaults.update(overrides)
    return Settings(**defaults)


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


class TestPasswordHashing:
    """Verify Argon2id hash / verify round-trip and rejection of wrong input."""

    def test_hash_and_verify_correct_password(self):
        """Hashing then verifying with the same password should return True."""
        hashed = hash_password("MySecureP4ss")
        assert verify_password("MySecureP4ss", hashed) is True

    def test_reject_wrong_password(self):
        """A different password must not verify against the hash."""
        hashed = hash_password("MySecureP4ss")
        assert verify_password("WrongPassword1", hashed) is False

    def test_hashes_are_unique(self):
        """Two calls with the same input should produce different hashes (random salt)."""
        h1 = hash_password("SameInput1")
        h2 = hash_password("SameInput1")
        assert h1 != h2


# ---------------------------------------------------------------------------
# JWT tokens
# ---------------------------------------------------------------------------


class TestJWTCreation:
    """Verify access and refresh tokens encode the expected claims."""

    def test_access_token_contains_correct_claims(self):
        """Access token payload should include sub, type=access, exp, iat, jti."""
        settings = _make_settings()
        token = create_access_token("user123", settings)
        payload = jose_jwt.decode(token, "test-secret", algorithms=["HS256"])

        assert payload["sub"] == "user123"
        assert payload["type"] == "access"
        assert "exp" in payload
        assert "iat" in payload
        assert "jti" in payload

    def test_refresh_token_contains_correct_claims(self):
        """Refresh token payload should include sub, type=refresh, exp, iat, jti."""
        settings = _make_settings()
        token = create_refresh_token("user456", settings)
        payload = jose_jwt.decode(token, "test-secret", algorithms=["HS256"])

        assert payload["sub"] == "user456"
        assert payload["type"] == "refresh"

    def test_access_token_expiry_matches_settings(self):
        """The exp claim should be roughly access_token_expire_minutes from now."""
        settings = _make_settings(jwt_access_token_expire_minutes=15)
        token = create_access_token("u1", settings)
        payload = jose_jwt.decode(token, "test-secret", algorithms=["HS256"])

        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = exp - now

        # Allow 5 seconds of tolerance for test execution time
        assert timedelta(minutes=14, seconds=55) < delta < timedelta(minutes=15, seconds=5)

    def test_refresh_token_expiry_matches_settings(self):
        """The exp claim should be roughly refresh_token_expire_days from now."""
        settings = _make_settings(jwt_refresh_token_expire_days=7)
        token = create_refresh_token("u1", settings)
        payload = jose_jwt.decode(token, "test-secret", algorithms=["HS256"])

        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = exp - now

        assert timedelta(days=6, hours=23, minutes=55) < delta < timedelta(days=7, seconds=5)


class TestJWTDecoding:
    """Verify decode_token accepts valid tokens and rejects bad ones."""

    def test_decode_valid_token(self):
        """A freshly created access token should decode without errors."""
        settings = _make_settings()
        token = create_access_token("user789", settings)
        payload = decode_token(token, settings)

        assert payload is not None
        assert payload["sub"] == "user789"
        assert payload["type"] == "access"

    def test_decode_expired_token_returns_none(self):
        """An expired token should return None (not raise)."""
        settings = _make_settings()
        expired_payload = {
            "sub": "expired_user",
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
            "type": "access",
            "iat": datetime.now(timezone.utc) - timedelta(hours=2),
            "jti": "test-jti",
        }
        token = jose_jwt.encode(expired_payload, "test-secret", algorithm="HS256")
        assert decode_token(token, settings) is None

    def test_decode_wrong_secret_returns_none(self):
        """A token signed with a different key should fail validation."""
        settings = _make_settings()
        wrong_key_settings = _make_settings(jwt_secret_key="wrong-secret")
        token = create_access_token("user", wrong_key_settings)

        assert decode_token(token, settings) is None

    def test_decode_tampered_token_returns_none(self):
        """Modifying the token string should invalidate the signature."""
        settings = _make_settings()
        token = create_access_token("user", settings)
        tampered = token[:-4] + "XXXX"

        assert decode_token(tampered, settings) is None

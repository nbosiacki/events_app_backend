"""
Shared test fixtures for the backend test suite.

Environment variables are set BEFORE any app imports because get_settings()
uses @lru_cache — the first call locks in the values. Setting APP_ENV=test
ensures we connect to stockholm_events_test instead of the dev database.

Fixtures provided:
    setup_db       – Autouse fixture that creates a fresh Motor client for
                     the current event loop, patches the app's mongodb module,
                     and tears down collections after every test for isolation.
    client         – httpx.AsyncClient wired to the FastAPI app via
                     ASGITransport (no real server started).
    test_user      – A pre-inserted user with known credentials
                     (email: test@example.com, password: TestPass1).
    auth_headers   – Authorization header dict containing a valid access
                     token for test_user.
    sample_event   – A pre-inserted event document with known data for
                     testing event-related endpoints.
"""

import os

# Must happen before any app import touches get_settings()
os.environ["APP_ENV"] = "test"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-testing-only"

import pytest
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import get_settings
from app.auth.password import hash_password
from app.auth.jwt import create_access_token

settings = get_settings()
assert settings.mongodb_db_name == "stockholm_events_test", (
    f"Expected test DB, got {settings.mongodb_db_name}"
)


@pytest.fixture(autouse=True)
async def setup_db():
    """Monkey-patch the app's mongodb module so all app code uses the test DB.

    Creates a fresh AsyncIOMotorClient for each test so it is bound to the
    current event loop (Motor ties itself to the loop at instantiation time,
    so a session-scoped client would break when pytest-asyncio creates a new
    loop for the next test).

    After the test completes, all documents in the events and users
    collections are deleted so each test starts with a clean slate.
    Indexes are recreated to match the production schema (unique source_url,
    unique email, etc.).
    """
    from app.db import mongodb

    mongo_client = AsyncIOMotorClient(settings.mongodb_url)
    mongodb.client = mongo_client
    mongodb.db = mongo_client[settings.mongodb_db_name]

    await mongodb.db.events.create_index("datetime_start")
    await mongodb.db.events.create_index("price.bucket")
    await mongodb.db.events.create_index("source_url", unique=True)
    await mongodb.db.users.create_index("email", unique=True)
    await mongodb.db.users.create_index("email_verification_token", sparse=True)
    await mongodb.db.users.create_index("password_reset_token", sparse=True)

    yield

    await mongodb.db.events.delete_many({})
    await mongodb.db.users.delete_many({})
    mongo_client.close()


@pytest.fixture
async def client():
    """Provide an async HTTP client connected to the FastAPI app.

    Uses httpx's ASGITransport so requests go directly through the ASGI
    interface — no TCP socket or running server needed. The base URL is
    arbitrary (http://test) since nothing actually listens on it.
    """
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def test_user(setup_db):
    """Insert a user with known credentials and return the raw document.

    Credentials:
        email:    test@example.com
        password: TestPass1

    The returned dict includes the MongoDB _id (as ObjectId). Use the
    auth_headers fixture to get a Bearer token for this user.
    """
    from app.db import mongodb

    user_doc = {
        "email": "test@example.com",
        "name": "Test User",
        "password_hash": hash_password("TestPass1"),
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
        "last_login": None,
        "auth_providers": [],
    }
    result = await mongodb.db.users.insert_one(user_doc)
    user_doc["_id"] = result.inserted_id
    return user_doc


@pytest.fixture
def auth_headers(test_user):
    """Return an Authorization header dict with a valid access token for test_user.

    Usage in tests:
        response = await client.get("/api/auth/me", headers=auth_headers)
    """
    token = create_access_token(str(test_user["_id"]), settings)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def sample_event(setup_db):
    """Insert a sample event and return the raw document.

    The event is a concert at Konserthuset on 2025-03-15, priced at 250 SEK
    (standard bucket). The returned dict includes the MongoDB _id.
    """
    from app.db import mongodb

    event_doc = {
        "title": "Test Concert",
        "description": "A great concert in Stockholm",
        "venue": {"name": "Konserthuset", "address": "Hötorget 8, Stockholm"},
        "datetime_start": datetime(2025, 3, 15, 19, 0),
        "datetime_end": datetime(2025, 3, 15, 22, 0),
        "price": {"amount": 250.0, "currency": "SEK", "bucket": "standard"},
        "source_url": "https://example.com/events/test-concert",
        "source_site": "example.com",
        "categories": ["music", "concert"],
        "scraped_at": datetime.now(timezone.utc),
    }
    result = await mongodb.db.events.insert_one(event_doc)
    event_doc["_id"] = result.inserted_id
    return event_doc

# Backend - Stockholm Events

FastAPI backend for the Stockholm Events app. Handles event storage, user authentication, Claude-powered web scraping, and personalized event recommendations.

## Table of Contents

- [Quick Start](#quick-start)
- [Directory Structure](#directory-structure)
- [Recommendation Engine](#recommendation-engine)
  - [Data Flow](#data-flow)
  - [Implicit Preference Analysis](#implicit-preference-analysis)
  - [Scoring Formula](#scoring-formula)
  - [Sort Behavior](#sort-behavior)
- [API Routes](#api-routes)
- [Authentication](#authentication)
- [Models](#models)
- [Agents (Scraper)](#agents-scraper)
- [Scripts](#scripts)
- [Testing](#testing)
- [Configuration](#configuration)

## Quick Start

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The API runs at http://localhost:8000. API docs at http://localhost:8000/docs.

## Directory Structure

```
backend/
├── app/
│   ├── main.py                    # FastAPI entry point, router registration
│   ├── config.py                  # Environment settings (Pydantic BaseSettings)
│   ├── api/
│   │   └── routes/
│   │       ├── events.py          # Event CRUD, filtering, sorting
│   │       ├── users.py           # User profile, preferences, like/attend
│   │       ├── auth.py            # Register, login, token refresh, password reset
│   │       └── scrape.py          # Scrape trigger endpoint
│   ├── auth/
│   │   ├── dependencies.py        # get_current_user, get_current_user_optional
│   │   ├── jwt.py                 # JWT creation and validation
│   │   ├── password.py            # bcrypt hashing and verification
│   │   └── schemas.py             # Auth request/response schemas
│   ├── services/
│   │   ├── preferences.py         # Implicit preference analysis
│   │   └── recommendations.py     # Event relevance scoring
│   ├── agents/
│   │   └── scraper.py             # Claude-powered web scraper (tool use)
│   ├── models/
│   │   ├── event.py               # Event, Price, Venue, EventResponse
│   │   └── user.py                # User, UserPreferences
│   └── db/
│       └── mongodb.py             # Motor async MongoDB connection
├── scripts/
│   ├── seed_data.py               # Dummy data generator
│   ├── run_scraper.py             # CLI scraper trigger
│   └── inspect_user.py            # CLI user inspector
├── tests/
│   ├── conftest.py                # Fixtures (async client, test DB, auth helpers)
│   ├── test_events_routes.py      # Event API tests (CRUD, filters, sorting)
│   ├── test_auth_routes.py        # Auth flow tests (register through lockout)
│   ├── test_users_routes.py       # User route tests (profile, prefs, like/attend)
│   ├── test_models.py             # Pydantic model tests
│   ├── test_auth_utils.py         # Password and JWT utility tests
│   ├── test_preferences.py        # Implicit preference analysis tests
│   ├── test_recommendations.py    # Recommendation scoring tests
│   ├── test_scraper_agent.py      # Scraper agent tests (mocked Anthropic)
│   ├── test_scrape_routes.py      # Scrape endpoint tests
│   └── test_seed_data.py          # Seed data generation tests
├── requirements.txt
├── pytest.ini
└── Dockerfile
```

## Recommendation Engine

The recommendation engine personalizes event listings for authenticated users. It combines **explicit preferences** (user-configured settings) with **implicit preferences** (derived from engagement history) to score and rank events.

### Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                     GET /api/events?sort=relevance              │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │   Authenticated?    │
                    └─────────┬───────────┘
                         yes  │  no
                    ┌─────────┘    └──────────────────┐
                    ▼                                  ▼
         ┌────────────────────┐              ┌─────────────────┐
         │  Fetch all events  │              │  Fallback to    │
         │  matching filters  │              │  time sort      │
         └────────┬───────────┘              └─────────────────┘
                  │
        ┌─────────┴──────────┐
        ▼                    ▼
┌──────────────┐   ┌──────────────────┐
│   Explicit   │   │    Implicit      │
│ Preferences  │   │   Preferences    │
│              │   │                  │
│ From user    │   │ From liked &     │
│ settings:    │   │ attended events: │
│ - categories │   │ - category       │
│ - max price  │   │   weights        │
│              │   │ - avg price      │
└──────┬───────┘   └────────┬─────────┘
       │                    │
       └────────┬───────────┘
                ▼
    ┌───────────────────────┐
    │    score_events()     │
    │                       │
    │  For each event:      │
    │  ┌─────────────────┐  │
    │  │ Category  0-50  │  │
    │  │ Price     0-30  │  │
    │  │ Freshness 0-20  │  │
    │  │ ─────────────── │  │
    │  │ Total     0-100 │  │
    │  └─────────────────┘  │
    └───────────┬───────────┘
                │
                ▼
    ┌───────────────────────┐
    │  Sort by score desc   │
    │  (ties: earliest      │
    │   datetime first)     │
    └───────────┬───────────┘
                │
                ▼
    ┌───────────────────────┐
    │  Apply skip/limit     │
    │  pagination            │
    └───────────────────────┘
```

### Implicit Preference Analysis

**File:** `app/services/preferences.py`

When a user requests relevance-sorted events, the system analyzes their engagement history:

1. **Fetch engaged events** — single `$in` query for all liked + attended event IDs
2. **Compute category weights** — count category occurrences across engaged events, weighted by engagement type:
   - Attended events: **1.5x weight** (stronger signal — user committed time/money)
   - Liked events: **1.0x weight** (interest signal)
   - Events in both lists use the higher weight (attended)
3. **Compute average price** — arithmetic mean of price amounts across all engaged events, mapped to a price bucket

**Example:** A user who attended 2 music events (1.5x each) and liked 1 art event (1.0x) produces:
```
category_weights: { "music": 3.0, "art": 1.0 }
avg_price: 175.0
avg_price_bucket: "standard"
```

### Scoring Formula

**File:** `app/services/recommendations.py`

Each event receives a score from 0 to 100, composed of three independent factors:

#### Category Match (0-50 points)

The higher of explicit and implicit category scores:

| Source | Condition | Score |
|--------|-----------|-------|
| Explicit | Event category in user's preferred list | **50** |
| Implicit | Event category in engagement history | **50 x (weight / max_weight)** |
| Neither | No category match | **0** |

Example: If implicit weights are `{music: 3.0, art: 1.0}` and the event is "art":
- Implicit score = 50 x (1.0 / 3.0) = **16.7**

#### Price Match (0-30 points)

Explicit preferences (max price bucket) take priority over implicit (avg price bucket):

**With explicit max price bucket** (not "premium"/any):

| Condition | Score |
|-----------|-------|
| Event at or below max bucket | **30** |
| Event one bucket above | **15** |
| Event two+ buckets above | **0** |

**With implicit avg price bucket only:**

| Distance from avg bucket | Score |
|--------------------------|-------|
| Same bucket | **30** |
| 1 bucket away | **20** |
| 2 buckets away | **10** |
| 3 buckets away | **0** |

**No price preferences:** neutral score of **15**.

#### Freshness (0-20 points)

Linear decay based on how soon the event occurs:

```
score = 20 x max(0, 1 - days_until_event / 14)
```

| Time until event | Score |
|------------------|-------|
| Today | **20** |
| +7 days | **10** |
| +14 days | **0** |
| Past events | **0** |

#### Combined Score Example

A music event, standard price, happening in 3 days, for a user who prefers music (explicit) and averages standard prices (implicit):

```
Category:  50  (explicit match)
Price:     30  (at max bucket)
Freshness: 15.7 (3/14 days decay)
────────────────
Total:     95.7
```

### Sort Behavior

The `GET /events` endpoint accepts a `sort` query parameter:

| Value | Behavior |
|-------|----------|
| `time` (default) | MongoDB sort by `datetime_start` ascending |
| `price_asc` | MongoDB sort by `price.amount` ascending |
| `price_desc` | MongoDB sort by `price.amount` descending |
| `relevance` | Score and rank by user preferences (auth required, falls back to `time` if unauthenticated) |

For `relevance` sort, pagination (skip/limit) is applied **after** scoring in Python rather than at the MongoDB level. This is necessary because scoring reorders results. Per-day result sets are small enough that this is not a performance concern.

## API Routes

### Events (`/api/events`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/events` | Optional | List events (filters: `date`, `price_bucket`, `sort`, `limit`, `skip`) |
| GET | `/api/events/{id}` | No | Get single event |
| POST | `/api/events` | No | Create event (duplicate check by `source_url`) |
| DELETE | `/api/events/{id}` | No | Delete event |

### Users (`/api/users`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/users/{id}` | Required | Get user profile (own profile only) |
| PUT | `/api/users/{id}/preferences` | Required | Update explicit preferences |
| POST | `/api/users/{id}/like/{event_id}` | Required | Like an event (idempotent) |
| DELETE | `/api/users/{id}/like/{event_id}` | Required | Unlike an event (idempotent) |
| POST | `/api/users/{id}/attend/{event_id}` | Required | Mark event as attended (idempotent) |

### Auth (`/api/auth`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/auth/register` | No | Register new account |
| POST | `/api/auth/login` | No | Login (OAuth2 password flow) |
| POST | `/api/auth/refresh` | No | Refresh access token |
| GET | `/api/auth/me` | Required | Get current user profile |
| POST | `/api/auth/forgot-password` | No | Request password reset token |
| POST | `/api/auth/reset-password` | No | Reset password with token |
| POST | `/api/auth/change-password` | Required | Change password |

### Scrape (`/api/scrape`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/scrape/trigger` | No | Trigger background scrape job |

## Authentication

JWT-based authentication with access/refresh token pair. See `app/auth/README.md` for detailed auth flow documentation.

- **Access token**: Short-lived (30 min default), sent in `Authorization: Bearer` header
- **Refresh token**: Longer-lived (7 days default), used to get new access tokens
- **Password hashing**: bcrypt via passlib
- **Account lockout**: 5 failed login attempts triggers 423 Locked response
- **Optional auth**: `get_current_user_optional` returns `None` instead of 401 when no token is present (used by relevance sort)

## Models

### Event (`app/models/event.py`)

- `EventBase`: Core fields (title, description, venue, datetime, price, categories, image_url)
- `EventCreate`: Extends base with source_url, source_site
- `EventResponse`: Adds id field, `from_mongo()` class method for ObjectId conversion
- `Price`: amount, currency (SEK), bucket (auto-computed via `from_amount()`)
- `Venue`: name, address, optional coordinates

### User (`app/models/user.py`)

- Core fields: email, name, hashed_password
- Auth fields: failed_login_attempts, locked_until, reset_token/expiry
- Engagement: liked_events (list of event IDs), attended_events (list of event IDs)
- `UserPreferences`: preferred_categories, max_price_bucket, preferred_areas

## Agents (Scraper)

### Scraper (`app/agents/scraper.py`)

Claude-powered web scraper using tool use. No hardcoded selectors — Claude reads page content and decides how to navigate.

**Tools provided to Claude:**
- `fetch_page(url)` — retrieve page HTML/text via httpx
- `extract_events(events)` — normalize found events into schema
- `done(summary)` — signal completion

Events are deduplicated by `source_url` — if a URL already exists in the database, the event is skipped.

## Scripts

| Script | Command | Description |
|--------|---------|-------------|
| `seed_data.py` | `python -m scripts.seed_data [--count N] [--clear] [--env dev\|test]` | Generate dummy events + dev user |
| `run_scraper.py` | `python -m scripts.run_scraper --source eventbrite [--parser-only]` | Trigger scraper from CLI (`--parser-only` disables Claude fallback) |
| `inspect_user.py` | `python -m scripts.inspect_user [--email EMAIL] [--env dev\|test]` | Inspect user account state |

## Testing

Tests use **pytest** with **pytest-asyncio** against a real MongoDB instance (`stockholm_events_test` database).

```bash
# Run all tests with coverage
APP_ENV=test pytest --cov=app -v

# Run a specific test file
APP_ENV=test pytest tests/test_recommendations.py -v

# Run a specific test class
APP_ENV=test pytest tests/test_events_routes.py::TestSortParameter -v
```

**Current:** 176 tests, 86% coverage.

| Test File | Coverage |
|-----------|----------|
| `test_models.py` | Price bucketing, Venue, EventResponse mapping |
| `test_auth_utils.py` | Password hashing, JWT creation/decoding |
| `test_events_routes.py` | Event CRUD, date/price filters, pagination, sort params |
| `test_auth_routes.py` | Register, login, token refresh, password reset, lockout |
| `test_users_routes.py` | User profile, preferences, like/unlike/attend |
| `test_preferences.py` | Implicit preference analysis (category weights, avg price) |
| `test_recommendations.py` | Scoring functions (category, price, freshness, combined) |
| `test_scraper_agent.py` | Scraper flow with mocked Anthropic API |
| `test_scrape_routes.py` | Scrape trigger endpoint |
| `test_seed_data.py` | Seed data templates and generation logic |

## Configuration

**File:** `app/config.py` (Pydantic `BaseSettings`, reads from `.env`)

| Variable | Description | Default |
|----------|-------------|---------|
| `APP_ENV` | Environment name (`development` / `test`) | `development` |
| `MONGODB_URL` | MongoDB connection string | `mongodb://localhost:27017` |
| `ANTHROPIC_API_KEY` | Claude API key for scraper fallback | (required) |
| `JWT_SECRET_KEY` | Secret for signing JWT tokens | (required) |
| `JWT_ALGORITHM` | JWT signing algorithm | `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Access token TTL | `30` |
| `REFRESH_TOKEN_EXPIRE_DAYS` | Refresh token TTL | `7` |

Database name is derived from `APP_ENV`: `stockholm_events_dev` or `stockholm_events_test`.

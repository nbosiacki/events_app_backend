"""
CLI script to generate invite codes for the beta.

Usage:
    python -m scripts.generate_invites --count 10
    python -m scripts.generate_invites --count 5 --env production
"""
import argparse
import secrets
import asyncio
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient


def generate_code() -> str:
    """Generate a random 8-character alphanumeric invite code."""
    return secrets.token_urlsafe(6).upper().replace("-", "").replace("_", "")[:8]


async def main():
    parser = argparse.ArgumentParser(description="Generate invite codes")
    parser.add_argument("--count", type=int, default=10, help="Number of codes to generate")
    parser.add_argument("--env", default="development", choices=["development", "production", "seed", "test"])
    args = parser.parse_args()

    import os
    os.environ["APP_ENV"] = args.env

    from app.config import get_settings
    settings = get_settings()

    client = AsyncIOMotorClient(settings.mongodb_url)
    db = client[settings.mongodb_db_name]

    # Ensure unique index exists
    await db.invite_codes.create_index("code", unique=True)

    codes = []
    for _ in range(args.count):
        code = generate_code()
        doc = {
            "code": code,
            "used": False,
            "created_at": datetime.now(timezone.utc),
            "used_by_email": None,
            "used_at": None,
        }
        await db.invite_codes.insert_one(doc)
        codes.append(code)

    client.close()

    print(f"\nGenerated {len(codes)} invite codes for {settings.mongodb_db_name}:\n")
    for code in codes:
        print(f"  {code}")
    print()


if __name__ == "__main__":
    asyncio.run(main())

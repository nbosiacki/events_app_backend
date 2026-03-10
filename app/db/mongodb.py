from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from app.config import get_settings

settings = get_settings()

client: AsyncIOMotorClient = None
db: AsyncIOMotorDatabase = None


async def connect_to_mongo():
    global client, db
    client = AsyncIOMotorClient(settings.mongodb_url)
    db = client[settings.mongodb_db_name]

    # Create indexes for events collection
    await db.events.create_index("datetime_start")
    await db.events.create_index("price.bucket")
    await db.events.create_index("source_url", unique=True)
    await db.events.create_index("content_hash", unique=True, sparse=True)

    # Create indexes for users collection (authentication)
    await db.users.create_index("email", unique=True)
    # Sparse indexes: only index documents where field exists (for password reset tokens)
    await db.users.create_index("email_verification_token", sparse=True)
    await db.users.create_index("password_reset_token", sparse=True)

    print(f"Connected to MongoDB: {settings.mongodb_db_name}")


async def close_mongo_connection():
    global client
    if client:
        client.close()
        print("Closed MongoDB connection")


def get_database() -> AsyncIOMotorDatabase:
    return db

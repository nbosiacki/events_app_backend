from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Environment
    app_env: Literal["development", "test"] = "development"

    # MongoDB
    mongodb_url: str = "mongodb://localhost:27017"
    mongodb_db_name: str = ""

    @model_validator(mode="after")
    def derive_db_name(self) -> "Settings":
        """Derive database name from app_env if not explicitly set."""
        if not self.mongodb_db_name:
            suffix = "dev" if self.app_env == "development" else "test"
            self.mongodb_db_name = f"stockholm_events_{suffix}"
        return self

    # Anthropic
    anthropic_api_key: str = ""

    # App settings
    debug: bool = True

    # JWT Configuration
    jwt_secret_key: str = ""  # MUST be set in .env, generate with: openssl rand -hex 32
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7

    # Security
    password_min_length: int = 8
    max_failed_login_attempts: int = 5
    account_lockout_minutes: int = 15

    # Admin
    admin_api_key: str = ""  # Set in .env to enable admin analytics endpoints

    # Frontend URL for email links
    frontend_url: str = "http://localhost:5173"

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()

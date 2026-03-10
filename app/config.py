from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Environment
    app_env: Literal["development", "test", "seed", "production"] = "development"

    # MongoDB (primary)
    mongodb_url: str = "mongodb://localhost:27017"
    mongodb_db_name: str = ""

    @model_validator(mode="after")
    def derive_db_name(self) -> "Settings":
        """Derive database name from app_env if not explicitly set."""
        if not self.mongodb_db_name:
            env_to_suffix = {"development": "dev", "test": "test", "seed": "seed", "production": "prod"}
            suffix = env_to_suffix[self.app_env]
            self.mongodb_db_name = f"sweden_events_{suffix}"
        return self

    # External scraper MongoDB (read-only, used by sync service)
    scraper_mongodb_url: str = ""
    scraper_mongodb_db_name: str = ""

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

    # CORS
    allowed_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # Admin
    admin_api_key: str = ""  # Set in .env to enable admin analytics endpoints

    # Frontend URL for email links
    frontend_url: str = "http://localhost:5173"

    # SMTP (email)
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""      # Gmail address
    smtp_password: str = ""  # Gmail App Password (not account password)
    from_email: str = ""     # Defaults to smtp_user if empty

    @model_validator(mode="after")
    def set_from_email(self) -> "Settings":
        """Default from_email to smtp_user if not explicitly set."""
        if not self.from_email and self.smtp_user:
            self.from_email = self.smtp_user
        return self

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()

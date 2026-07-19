from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "sqlite:///./data/job_outreach.db"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    openrouter_api_key: str = ""
    openrouter_model: str = "openrouter/free"
    openrouter_daily_request_limit: int = 25
    brave_api_key: str = ""
    brave_daily_query_limit: int = 30
    target_job_queries: str = "junior data engineer|junior software developer|data analyst"
    target_location: str = "Canada"
    research_department: str = ""
    gmail_credentials_file: Path = Path("./secrets/gmail_credentials.json")
    gmail_token_file: Path = Path("./secrets/gmail_token.json")
    gmail_query: str = (
        'newer_than:180d (from:jobalerts-noreply@linkedin.com OR subject:"job alert")'
    )
    user_profile_file: Path = Path("./data/profile.md")


@lru_cache
def get_settings() -> Settings:
    return Settings()

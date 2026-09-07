"""Typed application settings loaded from environment / .env.

Security note: no default secret is shipped for production. `SECRET_KEY` must be
supplied in every non-development environment or startup fails hard.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- core ---
    app_name: str = "Autonomous Research System"
    app_env: Environment = "development"
    debug: bool = False
    api_prefix: str = "/api/v1"
    host: str = "0.0.0.0"  # noqa: S104  # nosec B104
    port: int = 8000

    # --- security ---
    secret_key: str = Field(default="")
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 900  # 15 min
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 14
    stream_ticket_ttl_seconds: int = 60
    password_min_length: int = 12
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:8000"]
    trusted_hosts: list[str] = ["*"]
    csp: str = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
    )

    # --- rate limiting (token bucket) ---
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 120  # per window, per identity
    rate_limit_window_seconds: int = 60
    rate_limit_run_creation: int = 10  # research runs per hour per user
    rate_limit_run_window_seconds: int = 3600

    # --- persistence ---
    database_url: str = "sqlite+aiosqlite:///./data/ars.db"
    db_echo: bool = False

    # --- LLM provider ---
    llm_provider: Literal["mock", "anthropic"] = "mock"
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_model: str = "claude-sonnet-4-6"
    llm_max_tokens: int = 2000
    llm_temperature: float = 0.2
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 3

    # --- search provider ---
    search_provider: Literal["mock", "tavily", "brave"] = "mock"
    tavily_api_key: str = ""
    brave_api_key: str = ""
    search_timeout_seconds: float = 20.0

    # --- fetching / SSRF controls ---
    allow_private_network: bool = False
    fetch_timeout_seconds: float = 15.0
    fetch_max_bytes: int = 1_500_000
    allowed_url_schemes: list[str] = ["http", "https"]
    allowed_ports: list[int] = [80, 443]
    domain_denylist: list[str] = []

    # --- orchestration ---
    max_subquestions: int = 6
    max_sources_per_question: int = 5
    max_debate_rounds: int = 3
    max_concurrent_runs: int = 4
    run_timeout_seconds: int = 900
    min_claim_support: float = 0.5

    @field_validator("cors_origins", "trusted_hosts", "domain_denylist", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @model_validator(mode="after")
    def _enforce_secret(self) -> Settings:
        if not self.secret_key:
            if self.app_env in ("production", "staging"):
                raise ValueError("SECRET_KEY must be set outside development")
            object.__setattr__(self, "secret_key", secrets.token_urlsafe(48))
        if len(self.secret_key) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters")
        if self.app_env == "production":
            if self.debug:
                raise ValueError("DEBUG must be false in production")
            if "*" in self.cors_origins:
                raise ValueError("Wildcard CORS origin is not allowed in production")
            if self.allow_private_network:
                raise ValueError("ALLOW_PRIVATE_NETWORK must be false in production")
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "preview", "production"] = "development"
    data_mode: Literal["demo", "public"] = "demo"
    trading_mode: Literal["paper", "live"] = "paper"
    live_trading_enabled: bool = False
    kalshi_execution_enabled: bool = False
    polymarket_execution_enabled: bool = False
    global_kill_switch: bool = True
    database_url: str = "sqlite+aiosqlite:///./arb.db"
    redis_url: SecretStr | None = None
    admin_password_hash: SecretStr | None = None
    session_secret: SecretStr | None = None
    allowed_origins: str = (
        "http://localhost:5173,http://localhost:8000,http://127.0.0.1:5173,http://127.0.0.1:8000"
    )
    public_read_enabled: bool = False
    build_version: str = "development"
    operator_country: str = "US"
    operator_region: str = "GA"
    validation_interval_seconds: int = 10
    kalshi_environment: Literal["production", "demo"] = "production"
    kalshi_api_key: SecretStr | None = None
    kalshi_private_key: SecretStr | None = None
    polymarket_us_key_id: SecretStr | None = None
    polymarket_us_secret_key: SecretStr | None = None
    discovery_interval_seconds: int = 300
    book_poll_seconds: int = 2
    max_monitored_markets: int = 80
    request_rate: int = 5
    book_retention_days: int = 7
    history_retention_days: int = 180
    frontend_dist: str = "../frontend/dist"

    @model_validator(mode="after")
    def safety(self) -> "Settings":
        if (
            self.trading_mode != "paper"
            or self.live_trading_enabled
            or self.kalshi_execution_enabled
            or self.polymarket_execution_enabled
        ):
            raise ValueError("LIVE_EXECUTION_NOT_IMPLEMENTED: this release is read-only/paper")
        if self.environment == "production":
            if not self.admin_password_hash or not self.session_secret:
                raise ValueError("Production requires ADMIN_PASSWORD_HASH and SESSION_SECRET")
            if len(self.session_secret.get_secret_value()) < 32:
                raise ValueError("SESSION_SECRET must contain at least 32 characters")
            if not self.database_url.startswith(("postgresql", "postgres")):
                raise ValueError("Production requires PostgreSQL and explicit migrations")
            if not self.admin_password_hash.get_secret_value().startswith("$argon2id$"):
                raise ValueError("ADMIN_PASSWORD_HASH must be an Argon2id hash")
            if any(not x.startswith("https://") for x in self.origins):
                raise ValueError("Production origins must use HTTPS")
        if (
            min(
                self.discovery_interval_seconds,
                self.book_poll_seconds,
                self.max_monitored_markets,
                self.request_rate,
                self.book_retention_days,
                self.history_retention_days,
                self.validation_interval_seconds,
            )
            < 1
        ):
            raise ValueError("Intervals, budgets and retention must be positive")
        if self.request_rate > 10 or self.max_monitored_markets > 500:
            raise ValueError("Request budget or monitored universe exceeds supported limits")
        return self

    @property
    def origins(self) -> list[str]:
        return [x.strip() for x in self.allowed_origins.split(",") if x.strip()]

    @property
    def async_database_url(self) -> str:
        url = self.database_url
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return url.replace(prefix, "postgresql+asyncpg://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        fields = ",".join(
            ".".join(str(x) for x in error["loc"]) or "safety" for error in exc.errors()
        )
        raise RuntimeError(f"CONFIGURATION_INVALID:{fields}") from None

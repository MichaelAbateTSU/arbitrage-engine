from collections.abc import AsyncIterator

import pytest
from argon2 import PasswordHasher
from pydantic import SecretStr
from sqlalchemy import text

from app.arbitrage import detect
from app.config import Settings
from app.db import Base, create_database
from app.demo import demo_books, demo_markets
from app.domain import Opportunity, RiskSettings, now
from app.matching import match_markets
from app.service import rematch_all
from app.store import Store

TEST_PASSWORD = "test-only-operator-password-not-for-deployment"


@pytest.fixture
def scenario():
    markets = demo_markets()
    a, b = markets[:2]
    books = {(x.market_id, x.outcome): x for x in demo_books(markets, 0)}
    match = match_markets(a, b)
    risk = RiskSettings(kill_switch=False)
    return a, b, books, match, risk


@pytest.fixture
def opportunity(scenario) -> Opportunity:
    a, b, books, match, risk = scenario
    values, failures = detect(match, a, b, books, risk, now())
    assert not failures
    return next(x for x in values if x.risk_status == "qualified")


@pytest.fixture
async def store(tmp_path) -> AsyncIterator[Store]:
    settings = Settings(
        _env_file=None,
        environment="test",
        kalshi_api_key=None,
        kalshi_private_key=None,
        polymarket_us_key_id=None,
        polymarket_us_secret_key=None,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        admin_password_hash=SecretStr(PasswordHasher().hash(TEST_PASSWORD)),
        session_secret=SecretStr("test-only-session-secret-at-least-32-characters"),
    )
    engine, sessions = create_database(settings)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        await connection.execute(text("INSERT INTO alembic_version VALUES ('93ad7c201b46')"))
    result = Store(sessions, settings)
    await result.initialize()
    for market in demo_markets():
        await result.save_market(market)
    await result.save_books(demo_books(demo_markets(), 0))
    await rematch_all(result)
    yield result
    await engine.dispose()

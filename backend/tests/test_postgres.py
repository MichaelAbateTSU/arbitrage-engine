"""Optional real PostgreSQL regression suite; an isolated migrated database only."""

import asyncio
import os

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db import OpportunityRow, RiskRow, create_database
from app.demo import demo_books, demo_markets
from app.domain import D, Opportunity, PaperState, RiskSettings, now
from app.service import analysis_tick, rematch_all, reserve_trade
from app.store import Store


@pytest.mark.skipif(
    not os.environ.get("ARB_TEST_POSTGRES_URL"),
    reason="Requires explicitly isolated, migrated PostgreSQL database",
)
async def test_postgres_atomic_reservation_and_later_fill():
    settings = Settings(
        _env_file=None,
        database_url=os.environ["ARB_TEST_POSTGRES_URL"],
        environment="test",
        data_mode="demo",
        kalshi_api_key=None,
        kalshi_private_key=None,
        polymarket_us_key_id=None,
        polymarket_us_secret_key=None,
    )
    engine, sessions = create_database(settings)
    store = Store(sessions, settings)
    try:
        await asyncio.gather(store.initialize(), store.initialize())
        markets = demo_markets()
        for market in markets:
            await store.save_market(market)
        await store.save_books(demo_books(markets, 0))
        await rematch_all(store)
        async with sessions.begin() as session:
            row = await session.get(RiskRow, "demo")
            # This test measures database atomicity, not a subsecond execution SLO.
            row.payload = RiskSettings(
                kill_switch=False,
                max_quote_age_ms=60000,
            ).model_dump(mode="json")
        await analysis_tick(store)
        trades = await store.paper_trades()
        assert len(trades) == 4
        async with sessions() as session:
            row = await session.scalar(
                select(OpportunityRow).where(OpportunityRow.status == "qualified")
            )
            opportunity = Opportunity.model_validate(row.payload)
        assert await asyncio.gather(
            reserve_trade(store, opportunity),
            reserve_trade(store, opportunity),
        ) == [False, False]
        await asyncio.sleep(0.5)
        await store.save_books(demo_books(markets, 1))
        restarted = Store(sessions, settings)
        await analysis_tick(restarted)
        assert all(x.state == PaperState.HEDGED for x in await restarted.paper_trades())
        from app.analytics import summary

        report = await summary(restarted)
        assert report["fully_hedged"] == 4
        assert D(report["simulated_locked_profit"]) > 0
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    not os.environ.get("ARB_TEST_POSTGRES_URL"),
    reason="Requires explicitly isolated, migrated PostgreSQL database",
)
async def test_postgres_lock_timeout_rolls_back_without_holding_the_next_attempt():
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    settings = Settings(
        _env_file=None,
        database_url=os.environ["ARB_TEST_POSTGRES_URL"],
        environment="test",
        data_mode="demo",
        kalshi_api_key=None,
        kalshi_private_key=None,
        polymarket_us_key_id=None,
        polymarket_us_secret_key=None,
    )
    engine, _ = create_database(settings)
    lock = text("SELECT pg_advisory_xact_lock(5050992009218)")
    try:
        async with engine.begin() as first:
            assert await first.scalar(text("SHOW statement_timeout")) == "1min"
            assert await first.scalar(text("SHOW lock_timeout")) == "10s"
            assert await first.scalar(text("SHOW idle_in_transaction_session_timeout")) == "1min"
            await first.execute(lock)
            with pytest.raises(DBAPIError):
                async with engine.begin() as second:
                    await second.execute(text("SET LOCAL lock_timeout = '100ms'"))
                    await second.execute(lock)
        async with engine.begin() as retry:
            await retry.execute(lock)
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    not os.environ.get("ARB_TEST_POSTGRES_URL"),
    reason="Requires explicitly isolated, migrated PostgreSQL database",
)
async def test_postgres_focused_records_and_coverage_survive_restart():
    from app.focused import coverage_tick, focused_report, monitor_updates
    from app.validation import validation_settings, validation_tick

    settings = Settings(
        _env_file=None,
        database_url=os.environ["ARB_TEST_POSTGRES_URL"],
        environment="test",
        data_mode="demo",
        kalshi_api_key=None,
        kalshi_private_key=None,
        polymarket_us_key_id=None,
        polymarket_us_secret_key=None,
    )
    engine, sessions = create_database(settings)
    store = Store(sessions, settings)
    try:
        await store.initialize()
        for market in demo_markets():
            await store.save_market(market)
        await rematch_all(store)
        await store.save_books(demo_books(demo_markets(), 0))
        _, _, started = await validation_settings(store)
        await validation_tick(store)
        focused = await focused_report(store)
        assert focused["families"]
        assert len(focused["focused_pairs"]) <= 5
        identifier = focused["focused_pairs"][0]["books"][0]["market_id"]
        await monitor_updates(
            store,
            {
                identifier: {"selection": "selected", "selection_at": now().isoformat()},
            },
        )
        await monitor_updates(store, {identifier: {"subscription": "confirmed_by_data"}})
        await coverage_tick(store)
        await asyncio.sleep(0.5)
        restarted = Store(sessions, settings)
        await coverage_tick(restarted)
        report = await focused_report(restarted)
        assert D(report["coverage"]["pair_seconds"]["sampled"]) > 0
        assert (await validation_settings(restarted))[2] == started
        book = next(
            book
            for pair in report["focused_pairs"]
            for book in pair["books"]
            if book["market_id"] == identifier
        )
        assert book["monitoring"]["selection"] == "selected"
        assert book["monitoring"]["subscription"] == "confirmed_by_data"
    finally:
        await engine.dispose()

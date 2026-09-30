"""Optional real PostgreSQL regression suite; an isolated migrated database only."""

import asyncio
import os

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db import OpportunityRow, RiskRow, create_database
from app.demo import demo_books, demo_markets
from app.domain import D, Opportunity, PaperState, RiskSettings
from app.service import analysis_tick, rematch_all, reserve_trade
from app.store import Store


@pytest.mark.skipif(
    not os.environ.get("ARB_TEST_POSTGRES_URL"),
    reason="Requires explicitly isolated, migrated PostgreSQL database",
)
async def test_postgres_atomic_reservation_and_later_fill():
    settings = Settings(database_url=os.environ["ARB_TEST_POSTGRES_URL"], environment="test")
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

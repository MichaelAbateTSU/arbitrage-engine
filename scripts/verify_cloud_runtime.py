"""Read-only internal verification; prints no secrets or account data."""

import asyncio
import json
import time

from app.config import get_settings
from app.db import (
    HealthRow,
    MarketRow,
    MatchRow,
    OpportunityRow,
    PaperTradeRow,
    WorkerRow,
    create_database,
)
from app.domain import now
from app.store import Store
from sqlalchemy import func, select


async def main():
    settings = get_settings()
    engine, sessions = create_database(settings)
    store = Store(sessions, settings)
    try:
        async with sessions() as session:
            counts = {}
            for name, model in (
                ("markets", MarketRow),
                ("matches", MatchRow),
                ("opportunities", OpportunityRow),
                ("paper_trades", PaperTradeRow),
            ):
                counts[name] = await session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.source == settings.data_mode)
                )
            market_counts = (
                await session.execute(
                    select(MarketRow.venue, func.count())
                    .where(MarketRow.source == settings.data_mode)
                    .group_by(MarketRow.venue)
                )
            ).all()
            workers = (
                await session.scalars(
                    select(WorkerRow).where(WorkerRow.source == settings.data_mode)
                )
            ).all()
            health = (
                await session.scalars(
                    select(HealthRow).where(HealthRow.source == settings.data_mode)
                )
            ).all()
        books = await store.books()
        risk, revision = await store.risk()
        payload = {
            "at": now().isoformat(),
            "source": settings.data_mode,
            "mode": settings.trading_mode,
            "live_execution_enabled": settings.live_trading_enabled,
            "kill_switch": risk.kill_switch,
            "risk_revision": revision,
            "counts": counts,
            "markets_by_venue": {venue: count for venue, count in market_counts},
            "workers": [
                {
                    "role": worker.role,
                    "healthy": worker.expires_epoch > time.time(),
                    "at": worker.payload.get("at"),
                }
                for worker in workers
            ],
            "venues": [
                {
                    key: row.payload.get(key)
                    for key in (
                        "venue",
                        "feed",
                        "discovery",
                        "last_book",
                        "last_discovery",
                        "retrieved",
                        "monitored",
                        "reconnects",
                        "error",
                        "discovery_error",
                    )
                }
                for row in health
            ],
            "books": len(books),
            "fresh_synchronized_books": sum(
                book.connected
                and book.synchronized
                and book.age_ms(now()) <= risk.max_quote_age_ms
                for book in books.values()
            ),
        }
        print(json.dumps(payload, sort_keys=True))
        if settings.trading_mode != "paper" or settings.live_trading_enabled:
            raise RuntimeError("UNSAFE_CLOUD_MODE")
        if len(workers) != 3 or not all(
            worker.expires_epoch > time.time() for worker in workers
        ):
            raise RuntimeError("CLOUD_WORKER_HEARTBEAT_MISSING")
        if not counts["markets"]:
            raise RuntimeError("CLOUD_DISCOVERY_EMPTY")
        if len(market_counts) < 2:
            raise RuntimeError("CLOUD_CROSS_VENUE_DISCOVERY_INCOMPLETE")
        if not payload["fresh_synchronized_books"]:
            raise RuntimeError("CLOUD_FRESH_BOOKS_UNAVAILABLE")
    finally:
        await engine.dispose()


asyncio.run(main())

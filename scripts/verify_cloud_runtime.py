"""Read-only internal verification; prints no secrets or account data."""

import argparse
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
from app.validation import validation_report
from sqlalchemy import func, select, text


async def main(review_shortlist=False):
    settings = get_settings()
    engine, sessions = create_database(settings)
    store = Store(sessions, settings)
    try:
        async with sessions() as session:
            schema = await session.scalar(
                text("SELECT version_num FROM alembic_version")
            )
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
        validation = await validation_report(store)
        payload = {
            "at": now().isoformat(),
            "source": settings.data_mode,
            "mode": settings.trading_mode,
            "live_execution_enabled": settings.live_trading_enabled,
            "kill_switch": risk.kill_switch,
            "risk_revision": revision,
            "schema": schema,
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
            "validation": {
                key: validation[key]
                for key in (
                    "collection_started_at",
                    "candidate_pairs",
                    "directions",
                    "fresh_diagnostics",
                    "shadow_qualified_directions",
                    "operator_executable_directions",
                    "rejection_counts",
                    "approval_counts",
                    "distinct_opportunity_episodes",
                    "qualified_observations",
                    "shadow_baseline_trials",
                    "shadow_stress_trials",
                    "diagnostic_window_complete",
                    "automatic_live_permission",
                )
            },
            "eligibility": [
                {
                    key: item[key]
                    for key in (
                        "venue",
                        "price_access",
                        "account_read_access",
                        "order_permission",
                        "jurisdiction_status",
                        "open_order_eligible",
                        "probe_error",
                    )
                }
                for item in validation["eligibility"]
            ],
            "review_watchlist_pairs": len(validation["settings"]["selected_match_ids"]),
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
        if schema != "6e9f3a2c7d10":
            raise RuntimeError("CLOUD_VALIDATION_SCHEMA_MISSING")
        if not validation["fresh_diagnostics"] or not validation["candidate_pairs"]:
            raise RuntimeError("CLOUD_VALIDATION_DIAGNOSTICS_UNAVAILABLE")
        if not risk.kill_switch or validation["automatic_live_permission"]:
            raise RuntimeError("CLOUD_VALIDATION_SAFETY_FLAGS_CHANGED")
        if review_shortlist:
            markets = {market.id: market for market in await store.markets()}
            review = []
            for value in validation["shortlist"]:
                pair = {
                    "match_id": value["match_id"],
                    "event": value["event"],
                    "reasons": value["reasons"],
                    "settlement_proof": value["settlement_proof"],
                    "contracts": [],
                }
                for key in ("first_market_id", "second_market_id"):
                    market = markets[value[key]]
                    pair["contracts"].append(
                        {
                            "id": market.id,
                            "venue": market.venue,
                            "title": market.title,
                            "rules_hash": market.rules_hash,
                            "rules_text": market.rules_text,
                            "rules": market.rules.model_dump(mode="json"),
                            "start_time": str(market.start_time),
                            "start_time_verified": market.start_time_verified,
                            "fee": market.fee.model_dump(mode="json"),
                        }
                    )
                review.append(pair)
            print(json.dumps({"public_contract_review": review}, sort_keys=True))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-shortlist", action="store_true")
    asyncio.run(main(parser.parse_args().review_shortlist))

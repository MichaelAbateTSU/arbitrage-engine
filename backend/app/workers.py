import argparse
import asyncio
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from uuid import uuid4

import httpx
import structlog
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from websockets.exceptions import WebSocketException

from app.analytics import aggregate_day
from app.config import Settings, get_settings
from app.db import (
    AlertRow,
    MarketRow,
    ObservationRow,
    RejectionRow,
    SessionRow,
    SnapshotRow,
    SystemEventRow,
    create_database,
)
from app.demo import demo_books, demo_markets
from app.domain import Book, Side, Venue, now
from app.eligibility import refresh_account_evidence
from app.focused import coverage_tick, instrument_mapping_issue, monitor_updates
from app.pricing import BookIntegrityError
from app.service import analysis_tick, paper_tick, rematch_all
from app.shadow import shadow_tick
from app.store import Store
from app.telemetry import BOOKS, DISCOVERY, FAILURES, configure_logging
from app.validation import validation_tick
from app.venues.clients import InternationalClient, KalshiClient, USClient, VenueClient
from app.venues.http import PublicHTTP, VenueError
from app.venues.streams import international_stream, kalshi_stream, us_stream

log = structlog.get_logger()


async def guarded_loop(
    store: Store,
    role: str,
    action: Callable[[], Awaitable[None]],
    interval: float,
) -> None:
    while True:
        try:
            await action()
        except (SQLAlchemyError, ValueError, RuntimeError) as exc:
            code = (
                exc.code
                if isinstance(exc, VenueError)
                else (str(exc) if isinstance(exc, BookIntegrityError) else type(exc).__name__)
            )
            FAILURES.labels(role, code).inc()
            log.error("worker_iteration_failed", role=role, error_code=code)
            if not isinstance(exc, SQLAlchemyError):
                await store.system_error(role, code)
        await asyncio.sleep(interval)


async def run_discovery(store: Store, clients: list[VenueClient]) -> None:
    aliases = await store.aliases()
    for client in clients:
        retrieved = 0
        ids: set[str] = set()
        try:
            async for market in client.discover(aliases):
                if market.id in ids:
                    continue
                ids.add(market.id)
                await store.save_market(market)
                retrieved += 1
        except (VenueError, ValueError, KeyError) as exc:
            DISCOVERY.labels(client.venue, "partial").inc()
            await store.health(
                client.venue,
                discovery="partial",
                retrieved=retrieved,
                discovery_error=exc.code if isinstance(exc, VenueError) else type(exc).__name__,
            )
            log.warning(
                "discovery_partial",
                venue=client.venue,
                retrieved=retrieved,
                error_code=type(exc).__name__,
            )
            continue
        # Only a complete sweep may mark removed markets unavailable.
        async with store.sessions.begin() as session:
            rows = (
                await session.scalars(
                    select(MarketRow).where(
                        MarketRow.venue == client.venue,
                        MarketRow.source == store.source,
                    )
                )
            ).all()
            for row in rows:
                if row.id not in ids and row.status != "settled":
                    row.status = "closed"
                    row.payload = {**row.payload, "status": "closed", "tradable": False}
        DISCOVERY.labels(client.venue, "complete").inc()
        await store.health(
            client.venue,
            discovery="complete",
            retrieved=retrieved,
            last_discovery=now().isoformat(),
            discovery_error=None,
        )
    await rematch_all(store)


async def persist_stream(
    store: Store,
    client: VenueClient,
    generator: AsyncIterator[list[Book]],
    monitored: int,
    reconnects: int,
) -> None:
    pending: dict[tuple[str, Side], Book] = {}
    changed = asyncio.Event()

    async def receive() -> None:
        try:
            async for books in generator:
                for book in books:
                    pending[(book.market_id, book.outcome)] = book
                changed.set()
        finally:
            changed.set()

    reader = asyncio.create_task(receive())
    last_health = 0.0
    try:
        while True:
            await changed.wait()
            if reader.done():
                await reader
                if not pending:
                    return
            await asyncio.sleep(0.1)
            if reader.done():
                await reader
            books = list(pending.values())
            pending.clear()
            changed.clear()
            if not books:
                continue
            await store.save_books(books)
            BOOKS.labels(client.venue).inc(len(books))
            if time.monotonic() - last_health >= 2:
                await store.health(
                    client.venue,
                    feed="websocket",
                    last_book=max(book.received_at for book in books).isoformat(),
                    reconnects=reconnects,
                    monitored=monitored,
                    error=None,
                )
                last_health = time.monotonic()
            if reader.done():
                await reader
                return
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)


async def venue_feed(store: Store, client: VenueClient, settings: Settings) -> None:
    reconnects = 0
    while True:
        markets = [x for x in await store.markets() if x.venue == client.venue and x.tradable]
        from app.validation import validation_settings

        validation, _, _ = await validation_settings(store)
        watched = {
            identifier
            for match in await store.matches()
            if match.id in validation.selected_match_ids
            for identifier in (match.first_market_id, match.second_market_id)
        }
        markets.sort(
            key=lambda x: (
                x.id not in watched,
                not x.start_time_verified,
                x.start_time or now(),
                x.id,
            )
        )
        universe = markets
        mapping_issues = {market.id: instrument_mapping_issue(market) for market in universe}
        markets = [market for market in markets if mapping_issues[market.id] is None][
            : settings.max_monitored_markets
        ]
        generation = uuid4().hex
        selected_ids = {market.id for market in markets}
        await monitor_updates(
            store,
            {
                market.id: {
                    "selection": (
                        "excluded_invalid_mapping"
                        if mapping_issues[market.id]
                        else "selected"
                        if market.id in selected_ids
                        else "excluded_by_cap"
                    ),
                    "selection_at": now().isoformat(),
                    "generation": generation,
                    "priority": "focused_pair" if market.id in watched else "general_universe",
                    "instrument": market.external_id,
                    "cap": settings.max_monitored_markets,
                    "subscription": "not_requested",
                    "mapping": "invalid"
                    if mapping_issues[market.id]
                    else "metadata_only_unverified",
                    "mapping_error": mapping_issues[market.id],
                    "market_state": "unknown",
                    "stream_error": None,
                    "integrity_epoch": 0,
                }
                for market in universe
            },
        )
        if not markets:
            await store.health(client.venue, feed="waiting_for_discovery", monitored=0)
            await asyncio.sleep(5)
            continue
        ids = [x.id for x in markets]
        streaming = (
            client.venue == Venue.INTERNATIONAL
            or (
                client.venue == Venue.KALSHI
                and settings.kalshi_api_key
                and settings.kalshi_private_key
            )
            or (
                client.venue == Venue.US
                and settings.polymarket_us_key_id
                and settings.polymarket_us_secret_key
            )
        )
        try:
            if streaming:
                events: dict[str, dict[str, object]] = {}
                epochs: dict[str, int] = {}

                def observe(
                    identifiers: list[str],
                    event: str,
                    queue: dict[str, dict[str, object]] = events,
                    current_generation: str = generation,
                    current_epochs: dict[str, int] = epochs,
                ) -> None:
                    for identifier in identifiers:
                        value = queue.setdefault(identifier, {})
                        value["generation"] = current_generation
                        if event == "market_not_open":
                            current_epochs[identifier] = current_epochs.get(identifier, 0) + 1
                        value["integrity_epoch"] = current_epochs.get(identifier, 0)
                        if event.startswith("subscription_"):
                            value["subscription"] = event.removeprefix("subscription_")
                            value["subscription_at"] = now().isoformat()
                        elif event == "book_received":
                            value["subscription"] = "confirmed_by_data"
                            value["mapping"] = "verified_by_stream_data"
                            value["last_stream_book_at"] = now().isoformat()
                            value["market_state"] = "open"
                        elif event == "market_not_open":
                            value["market_state"] = "not_open"

                async def flush_monitoring(
                    queue: dict[str, dict[str, object]] = events,
                ) -> None:
                    while True:
                        await asyncio.sleep(0.5)
                        values = dict(queue)
                        queue.clear()
                        await monitor_updates(store, values)

                generator = (
                    international_stream(markets, observe)
                    if client.venue == Venue.INTERNATIONAL
                    else (
                        kalshi_stream(settings, markets, observe)
                        if client.venue == Venue.KALSHI
                        else us_stream(settings, markets, observe)
                    )
                )
                # Periodic reconnect requests an authoritative snapshot and revised
                # universe. No REST snapshot is raced against an unsequenced delta.
                async with asyncio.timeout(settings.discovery_interval_seconds):
                    writer = asyncio.create_task(flush_monitoring())
                    persistence = asyncio.create_task(
                        persist_stream(store, client, generator, len(markets), reconnects)
                    )
                    try:
                        completed, _ = await asyncio.wait(
                            (writer, persistence), return_when=asyncio.FIRST_COMPLETED
                        )
                        for task in completed:
                            await task
                        if writer in completed:
                            raise RuntimeError("MONITORING_WRITER_STOPPED")
                    finally:
                        writer.cancel()
                        persistence.cancel()
                        await asyncio.gather(writer, persistence, return_exceptions=True)
                        await monitor_updates(store, events)
            else:
                for market in markets:
                    mapping_error = instrument_mapping_issue(market)
                    if mapping_error:
                        await monitor_updates(
                            store,
                            {
                                market.id: {
                                    "mapping": "invalid",
                                    "mapping_error": mapping_error,
                                    "rest_probe": {
                                        "at": now().isoformat(),
                                        "status": "not_sent_invalid_mapping",
                                        "error_code": mapping_error,
                                    },
                                }
                            },
                        )
                        continue
                    books = await client.get_orderbooks(market)
                    await store.save_books(books)
                    await monitor_updates(
                        store,
                        {
                            market.id: {
                                "subscription": "rest_only",
                                "last_rest_book_at": now().isoformat(),
                            }
                        },
                    )
                    BOOKS.labels(client.venue).inc(len(books))
                    await store.health(
                        client.venue,
                        feed="rest_polling_credentials_needed_for_websocket",
                        last_book=now().isoformat(),
                        monitored=len(markets),
                        error=None,
                    )
                await asyncio.sleep(settings.book_poll_seconds)
                continue
        except TimeoutError:
            await store.invalidate_books(ids)
            await store.health(client.venue, feed="resnapshot", reconnects=reconnects)
            await monitor_updates(
                store,
                {
                    identifier: {
                        "stream_error": "PERIODIC_AUTHORITATIVE_RESNAPSHOT",
                    }
                    for identifier in ids
                },
            )
        except (VenueError, ValueError, KeyError, WebSocketException, OSError) as exc:
            reconnects += 1
            await store.invalidate_books(ids)
            code = exc.code if isinstance(exc, VenueError) else type(exc).__name__
            FAILURES.labels("market-data", code).inc()
            await store.health(client.venue, feed="disconnected", error=code, reconnects=reconnects)
            await monitor_updates(
                store,
                {
                    identifier: {
                        "stream_error": code,
                    }
                    for identifier in ids
                },
            )
            log.warning("venue_feed_failed", venue=client.venue, error_code=code)
            await asyncio.sleep(min(60, 2 ** min(reconnects, 6)) + random.uniform(0, 1))
        finally:
            if streaming:
                await store.invalidate_books(ids)


async def market_data(store: Store, settings: Settings) -> None:
    if settings.data_mode == "demo":
        tick = 0
        while True:
            markets = demo_markets()
            for market in markets:
                await store.save_market(market)
            await store.save_books(demo_books(markets, tick))
            if tick % 10 == 0:
                await rematch_all(store)
            for venue in Venue:
                await store.health(
                    venue,
                    feed="synthetic_demo",
                    discovery="complete",
                    last_book=now().isoformat(),
                    last_discovery=now().isoformat(),
                    reconnects=0,
                )
            tick += 1
            await asyncio.sleep(0.5)
    else:
        async with httpx.AsyncClient(follow_redirects=False) as http:
            transport = PublicHTTP(http, settings.request_rate)
            clients: list[VenueClient] = [
                KalshiClient(transport, settings),
                USClient(transport),
                InternationalClient(transport),
            ]

            async def discovery() -> None:
                await run_discovery(store, clients)

            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(
                    guarded_loop(
                        store,
                        "focused-book-probes",
                        lambda: focused_book_probes(store, clients),
                        60,
                    )
                )
                tasks.create_task(
                    guarded_loop(
                        store,
                        "eligibility",
                        lambda: refresh_account_evidence(store, http),
                        300,
                    )
                )
                tasks.create_task(
                    guarded_loop(
                        store,
                        "market-data",
                        discovery,
                        settings.discovery_interval_seconds,
                    )
                )
                for client in clients:
                    tasks.create_task(venue_feed(store, client, settings))


async def focused_book_probes(store: Store, clients: list[VenueClient]) -> None:
    from app.validation import validation_settings

    config, _, _ = await validation_settings(store)
    if not config.enabled:
        return
    matches = [m for m in await store.matches() if m.id in config.selected_match_ids]
    ids = {identifier for m in matches for identifier in (m.first_market_id, m.second_market_id)}
    markets = [m for m in await store.markets() if m.id in ids]
    books = await store.books(list(ids))
    risk, _ = await store.risk()
    by_venue = {client.venue: client for client in clients}
    for market in markets:
        current = [books.get((market.id, side)) for side in Side]
        if all(
            book is not None
            and book.connected
            and book.synchronized
            and book.age_ms(now()) <= risk.max_quote_age_ms
            for book in current
        ):
            continue
        probe: dict[str, object] = {"at": now().isoformat()}
        try:
            observed = await by_venue[market.venue].get_orderbooks(market)
            probe.update(
                status="snapshot_received",
                outcomes=[
                    {
                        "side": book.outcome,
                        "bids": len(book.bids),
                        "asks": len(book.asks),
                        "age_ms": book.age_ms(now()),
                        "received_at": book.received_at.isoformat(),
                    }
                    for book in observed
                ],
            )
        except (VenueError, ValueError, KeyError) as exc:
            code = exc.code if isinstance(exc, VenueError) else type(exc).__name__
            probe.update(status="failed", error_code=code)
            await store.system_error("focused-book-probes", code)
        # An independent REST probe is evidence, not a replacement for a sequenced
        # WebSocket snapshot; its original venue timestamp is never refreshed.
        await monitor_updates(
            store,
            {
                market.id: {
                    "rest_probe": probe,
                    **(
                        {"mapping": "verified_by_public_book_endpoint"}
                        if probe["status"] == "snapshot_received"
                        else {}
                    ),
                }
            },
        )


async def maintenance_tick(store: Store) -> None:
    if store.source == "public":
        await refresh_settlements(store)
    yesterday = (now() - timedelta(days=1)).date().isoformat()
    await aggregate_day(store, yesterday)
    async with store.sessions.begin() as session:
        for model, days in (
            (SnapshotRow, store.settings.book_retention_days),
            (RejectionRow, store.settings.history_retention_days),
            (ObservationRow, store.settings.history_retention_days),
            (SystemEventRow, store.settings.history_retention_days),
        ):
            await session.execute(
                delete(model).where(
                    model.source == store.source, model.created_at < now() - timedelta(days=days)
                )
            )
        await session.execute(
            delete(SessionRow).where(SessionRow.expires_epoch < int(now().timestamp()))
        )
        from app.db import WorkerRow

        workers = (await session.scalars(select(WorkerRow))).all()
        for worker in workers:
            if worker.role != "maintenance" and worker.expires_epoch < int(now().timestamp()):
                identifier = f"heartbeat:{worker.role}:{now().date()}"
                if await session.get(AlertRow, identifier) is None:
                    session.add(
                        AlertRow(
                            id=identifier,
                            source=store.source,
                            severity="critical",
                            payload={"code": "WORKER_HEARTBEAT_MISSING", "role": worker.role},
                        )
                    )


async def refresh_settlements(store: Store) -> None:
    from app.db import OpportunityRow, ShadowRow
    from app.domain import Opportunity

    markets = {market.id: market for market in await store.markets()}
    identifiers: set[str] = set()
    trades = await store.paper_trades()
    async with store.sessions() as session:
        for trade in trades:
            if trade.state not in ("HEDGED", "UNHEDGED"):
                continue
            row = await session.get(OpportunityRow, trade.opportunity_id)
            if row:
                opportunity = Opportunity.model_validate(row.payload)
                identifiers.update([opportunity.first_market_id, opportunity.second_market_id])
        shadows = (
            await session.scalars(
                select(ShadowRow).where(
                    ShadowRow.source == store.source,
                    ShadowRow.state.in_(("HEDGED", "HEDGED_AFTER_UNWIND", "RESIDUAL_EXPOSURE")),
                )
            )
        ).all()
        for shadow in shadows:
            signal = shadow.payload["validation"]
            identifiers.update([signal["first_market_id"], signal["second_market_id"]])
    async with httpx.AsyncClient(follow_redirects=False) as http:
        transport = PublicHTTP(http, store.settings.request_rate)
        clients: dict[Venue, VenueClient] = {
            Venue.KALSHI: KalshiClient(transport, store.settings),
            Venue.US: USClient(transport),
            Venue.INTERNATIONAL: InternationalClient(transport),
        }
        for identifier in identifiers:
            market = markets.get(identifier)
            if market is None or market.result is not None:
                continue
            try:
                payout = await clients[market.venue].get_settlement(market)
            except (VenueError, ValueError, KeyError) as exc:
                code = exc.code if isinstance(exc, VenueError) else type(exc).__name__
                await store.system_error("settlement", code)
                log.warning("settlement_unavailable", market_id=identifier, error_code=code)
                continue
            if payout is not None:
                market.result = payout
                market.status = "settled"
                market.tradable = False
                await store.save_market(market)


async def role_runner(role: str, store: Store, settings: Settings) -> None:
    owner = uuid4().hex
    while not await store.heartbeat(role, owner, acquire=True):
        log.info("worker_standby_waiting_for_lease", role=role)
        await asyncio.sleep(5)

    async def pulse() -> None:
        while True:
            await asyncio.sleep(5)
            if not await store.heartbeat(role, owner):
                raise RuntimeError("WORKER_LEASE_LOST")

    try:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(pulse())
            if role == "market-data":
                tasks.create_task(market_data(store, settings))
            elif role == "analysis":
                tasks.create_task(
                    guarded_loop(store, "observation-coverage", lambda: coverage_tick(store), 0.5)
                )
                tasks.create_task(
                    guarded_loop(
                        store,
                        "validation",
                        lambda: validation_tick(store),
                        settings.validation_interval_seconds,
                    )
                )
                tasks.create_task(
                    guarded_loop(
                        store,
                        "shadow",
                        lambda: shadow_tick(store),
                        0.5,
                    )
                )
                tasks.create_task(
                    guarded_loop(
                        store,
                        role,
                        lambda: analysis_tick(store, process_paper=False),
                        0.2,
                    )
                )
                tasks.create_task(guarded_loop(store, "paper", lambda: paper_tick(store), 0.1))
            elif role == "maintenance":
                tasks.create_task(guarded_loop(store, role, lambda: maintenance_tick(store), 60))
            else:
                raise ValueError("UNKNOWN_WORKER_ROLE")
    finally:
        await store.release(role, owner)


async def main(role: str) -> None:
    configure_logging()
    settings = get_settings()
    engine, sessions = create_database(settings)
    store = Store(sessions, settings)
    for attempt in range(60):
        try:
            await store.initialize()
            break
        except SQLAlchemyError:
            log.warning("worker_waiting_for_database_and_migrations", role=role, attempt=attempt)
            await asyncio.sleep(3)
    else:
        raise RuntimeError("DATABASE_OR_MIGRATIONS_UNAVAILABLE")
    roles = ["market-data", "analysis", "maintenance"] if role == "all" else [role]
    tasks = [asyncio.create_task(role_runner(item, store, settings)) for item in roles]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=["market-data", "analysis", "maintenance", "all"])
    asyncio.run(main(parser.parse_args().role))

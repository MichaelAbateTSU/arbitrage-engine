import asyncio
import socket
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.demo import demo_books, demo_markets
from app.domain import Venue
from app.workers import initialize_worker, persist_stream


async def test_stream_reader_does_not_wait_for_database():
    consumed = []
    stored = []

    class Store:
        async def save_books(self, books):
            await asyncio.sleep(0.2)
            stored.extend(books)

        async def health(self, venue, **fields):
            return None

    class Client:
        venue = Venue.KALSHI

    async def source():
        for sequence in range(25):
            consumed.append(sequence)
            yield demo_books(demo_markets()[:1], sequence)
        await asyncio.sleep(1)

    task = asyncio.create_task(persist_stream(Store(), Client(), source(), 1, 0))
    try:
        await asyncio.sleep(0.05)
        assert consumed == list(range(25))
        await asyncio.sleep(0.4)
        assert stored
        assert all(book.sequence == 24 for book in stored)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_finished_stream_flushes_last_books():
    stored = []

    class Store:
        async def save_books(self, books):
            stored.extend(books)

        async def health(self, venue, **fields):
            return None

    class Client:
        venue = Venue.KALSHI

    async def source():
        yield demo_books(demo_markets()[:1], 7)

    await persist_stream(Store(), Client(), source(), 1, 0)
    assert stored
    assert all(book.sequence == 7 for book in stored)


async def test_reader_failure_is_not_hidden_by_coalescing():
    class Store:
        async def save_books(self, books):
            pytest.fail("Invalidated pending books must not be written")

        async def health(self, venue, **fields):
            pytest.fail("Reader failure must propagate")

    class Client:
        venue = Venue.KALSHI

    async def source():
        yield demo_books(demo_markets()[:1], 1)
        raise ValueError("SEQUENCE_GAP")

    with pytest.raises(ValueError, match="SEQUENCE_GAP"):
        await persist_stream(Store(), Client(), source(), 1, 0)


async def test_worker_waits_for_transient_database_dns_failure():
    store = AsyncMock()
    store.initialize.side_effect = [socket.gaierror(-2, "not resolved"), None]
    await initialize_worker(store, "analysis", attempts=2, delay=0)
    assert store.initialize.await_count == 2


async def test_worker_does_not_claim_success_when_database_stays_unavailable():
    store = AsyncMock()
    store.initialize.side_effect = socket.gaierror(-2, "not resolved")
    with pytest.raises(RuntimeError, match="DATABASE_OR_MIGRATIONS_UNAVAILABLE"):
        await initialize_worker(store, "analysis", attempts=2, delay=0)
    assert store.initialize.await_count == 2


def test_render_source_commit_overrides_stale_manual_build_version(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    monkeypatch.setenv("BUILD_VERSION", "obsolete-release")
    assert Settings(_env_file=None).build_version == "a" * 40
    assert Settings(_env_file=None, build_version="explicit-test").build_version == "explicit-test"


async def test_worker_startup_failure_disposes_engine(monkeypatch):
    from app import workers

    engine = AsyncMock()
    initialize = AsyncMock(side_effect=RuntimeError("DATABASE_OR_MIGRATIONS_UNAVAILABLE"))
    monkeypatch.setattr(workers, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(workers, "create_database", lambda _: (engine, None))
    monkeypatch.setattr(workers, "initialize_worker", initialize)
    with pytest.raises(RuntimeError, match="DATABASE_OR_MIGRATIONS_UNAVAILABLE"):
        await workers.main("analysis")
    engine.dispose.assert_awaited_once()

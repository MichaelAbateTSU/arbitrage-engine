import asyncio

from app.demo import demo_books, demo_markets
from app.domain import Venue
from app.workers import persist_stream


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

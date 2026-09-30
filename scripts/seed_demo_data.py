import asyncio

from app.config import get_settings
from app.db import create_database
from app.demo import demo_books, demo_markets
from app.service import analysis_tick, rematch_all
from app.store import Store


async def main():
    settings = get_settings()
    if settings.data_mode != "demo":
        raise RuntimeError("DEMO_SEED_FORBIDDEN_IN_PUBLIC_MODE")
    engine, sessions = create_database(settings)
    store = Store(sessions, settings)
    try:
        await store.initialize()
        markets = demo_markets()
        for market in markets:
            await store.save_market(market)
        await store.save_books(demo_books(markets, 0))
        await rematch_all(store)
        await analysis_tick(store)
        print("Seeded isolated synthetic demo; kill switch remains unchanged.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

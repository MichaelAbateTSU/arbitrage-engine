import argparse
import asyncio
import json

from app.config import get_settings
from app.db import OpportunityRow, SnapshotRow, create_database
from sqlalchemy import select

parser = argparse.ArgumentParser()
parser.add_argument("--books", action="store_true")
args = parser.parse_args()


async def main():
    settings = get_settings()
    engine, sessions = create_database(settings)
    model = SnapshotRow if args.books else OpportunityRow
    try:
        async with sessions() as session:
            stream = await session.stream_scalars(
                select(model)
                .where(model.source == settings.data_mode)
                .order_by(model.created_at)
            )
            async for row in stream:
                print(json.dumps(row.payload))
    finally:
        await engine.dispose()


asyncio.run(main())

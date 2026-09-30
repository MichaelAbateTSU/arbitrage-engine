import argparse
import asyncio
import json
from pathlib import Path

from app.domain import Book
from app.venues.http import decode

parser = argparse.ArgumentParser(
    description="Replay exported normalized book JSONL, not a predictive backtest"
)
parser.add_argument("file", type=Path)
parser.add_argument("--speed", type=float, default=1)
parser.add_argument("--step", action="store_true")
args = parser.parse_args()
if args.speed <= 0:
    parser.error("Speed must be positive")


async def main():
    previous = None
    for line in args.file.read_text(encoding="utf-8").splitlines():
        book = Book.model_validate(decode(line))
        if args.step:
            input("Enter to apply next observation...")
        elif previous:
            await asyncio.sleep(
                max(0, (book.received_at - previous).total_seconds() / args.speed)
            )
        print(json.dumps(book.model_dump(mode="json")))
        previous = book.received_at


asyncio.run(main())

import asyncio
import base64
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
from websockets.asyncio.client import connect

from app.config import Settings
from app.domain import Book, D, Level, Market, now
from app.pricing import BookIntegrityError, LocalBook, complementary_book, ordered
from app.venues.clients import us_books
from app.venues.http import VenueError, decode
from app.venues.schemas import USBook


def kalshi_headers(settings: Settings, path: str = "/trade-api/ws/v2") -> dict[str, str]:
    if not settings.kalshi_api_key or not settings.kalshi_private_key:
        raise VenueError("KALSHI_STREAM_CREDENTIALS_REQUIRED")
    encoded = settings.kalshi_private_key.get_secret_value().replace("\\n", "\n").strip().encode()
    try:
        if encoded.startswith(b"-----BEGIN"):
            key = serialization.load_pem_private_key(encoded, password=None)
        else:
            key = serialization.load_der_private_key(
                base64.b64decode(encoded, validate=True), password=None
            )
    except (ValueError, TypeError):
        raise VenueError("INVALID_KALSHI_PRIVATE_KEY_FORMAT") from None
    if not isinstance(key, (rsa.RSAPrivateKey, ed25519.Ed25519PrivateKey)):
        raise VenueError("INVALID_KALSHI_KEY_TYPE")
    timestamp = str(int(time.time() * 1000))
    message = f"{timestamp}GET{path}".encode()
    if isinstance(key, ed25519.Ed25519PrivateKey):
        signature = key.sign(message)
    else:
        signature = key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
    return {
        "KALSHI-ACCESS-KEY": settings.kalshi_api_key.get_secret_value(),
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
    }


def us_headers(settings: Settings) -> dict[str, str]:
    if not settings.polymarket_us_key_id or not settings.polymarket_us_secret_key:
        raise VenueError("US_STREAM_CREDENTIALS_REQUIRED")
    key = ed25519.Ed25519PrivateKey.from_private_bytes(
        base64.b64decode(settings.polymarket_us_secret_key.get_secret_value(), validate=True)[:32]
    )
    timestamp = str(int(time.time() * 1000))
    signature = key.sign(f"{timestamp}GET/v1/ws/markets".encode())
    return {
        "X-PM-Access-Key": settings.polymarket_us_key_id.get_secret_value(),
        "X-PM-Timestamp": timestamp,
        "X-PM-Signature": base64.b64encode(signature).decode(),
    }


class KalshiStreamState:
    def __init__(self, markets: list[Market]) -> None:
        self.markets = {x.external_id: x for x in markets}
        self.sequences: dict[int, int] = {}
        self.levels: dict[str, dict[str, dict[D, D]]] = {}

    def apply(self, data: dict[str, Any]) -> list[Book]:
        kind = data["type"]
        if kind == "error":
            raise VenueError("KALSHI_SUBSCRIPTION_ERROR")
        if "sid" in data and "seq" in data:
            sid, seq = int(data["sid"]), int(data["seq"])
            previous = self.sequences.get(sid)
            if previous is not None:
                if seq == previous:
                    return []
                if seq != previous + 1:
                    raise BookIntegrityError("SEQUENCE_GAP")
            self.sequences[sid] = seq
        if kind not in ("orderbook_snapshot", "orderbook_delta", "market_lifecycle_v2"):
            return []
        seq = int(data["seq"])
        msg = data["msg"]
        ticker = msg["market_ticker"]
        if kind == "market_lifecycle_v2":
            market = self.markets.get(ticker)
            if market is None:
                return []
            # Any lifecycle change needs authoritative metadata before reuse,
            # including activation, rescheduling and changed price grids.
            market.tradable = False
            market.status = "paused"
            return list(
                complementary_book(
                    market.id,
                    [],
                    [],
                    connected=False,
                    synchronized=False,
                    received_at=now(),
                    transport="websocket",
                    source=market.source,
                )
            )
        market = self.markets[ticker]
        if kind == "orderbook_snapshot":
            self.levels[ticker] = {
                side: {D(p): D(q) for p, q in msg.get(f"{side}_dollars_fp", []) if D(q) > 0}
                for side in ("yes", "no")
            }
        else:
            if ticker not in self.levels:
                raise BookIntegrityError("SNAPSHOT_REQUIRED")
            side = msg["side"]
            if side not in ("yes", "no"):
                raise BookIntegrityError("UNKNOWN_OUTCOME_SIDE")
            price, delta = D(msg["price_dollars"]), D(msg["delta_fp"])
            ladder = self.levels[ticker][side]
            size = ladder.get(price, D("0")) + delta
            if size < 0:
                raise BookIntegrityError("NEGATIVE_BOOK_QUANTITY")
            if size:
                ladder[price] = size
            else:
                ladder.pop(price, None)
        ladders = self.levels[ticker]
        exchange_at = None
        if data.get("sending_ts_ms") is not None:
            exchange_at = datetime.fromtimestamp(int(data["sending_ts_ms"]) / 1000, UTC)
        return list(
            complementary_book(
                market.id,
                [Level(price=p, quantity=q) for p, q in ladders["yes"].items()],
                [Level(price=p, quantity=q) for p, q in ladders["no"].items()],
                sequence=seq,
                exchange_at=exchange_at,
                received_at=now(),
                transport="websocket",
                source=market.source,
                connected=market.tradable,
                synchronized=market.tradable,
            )
        )


async def kalshi_stream(settings: Settings, markets: list[Market]) -> AsyncIterator[list[Book]]:
    url = (
        "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
        if settings.kalshi_environment == "production"
        else "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"
    )
    state = KalshiStreamState(markets)
    async with connect(
        url,
        additional_headers=kalshi_headers(settings),
        open_timeout=15,
        ping_interval=10,
        ping_timeout=10,
        max_size=2_000_000,
    ) as socket:
        await socket.send(
            json.dumps(
                {
                    "id": 1,
                    "cmd": "subscribe",
                    "params": {
                        "channels": ["orderbook_delta"],
                        "market_tickers": [x.external_id for x in markets],
                    },
                }
            )
        )
        await socket.send(
            json.dumps(
                {
                    "id": 2,
                    "cmd": "subscribe",
                    "params": {"channels": ["market_lifecycle_v2"]},
                }
            )
        )
        while True:
            raw = await asyncio.wait_for(socket.recv(), timeout=30)
            books = state.apply(decode(str(raw)))
            if books:
                yield books


class InternationalStreamState:
    def __init__(self, markets: list[Market]) -> None:
        self.tokens = {
            token: (market, side) for market in markets for side, token in market.token_ids.items()
        }
        self.books: dict[str, LocalBook] = {}

    def apply(self, data: dict[str, Any]) -> list[Book]:
        kind = data["event_type"]
        timestamp = datetime.fromtimestamp(int(data["timestamp"]) / 1000, UTC)
        if kind == "book":
            token = data["asset_id"]
            market, side = self.tokens[token]
            book = Book(
                market_id=market.id,
                outcome=side,
                bids=ordered(
                    [Level(price=x["price"], quantity=x["size"]) for x in data["bids"]], True
                ),
                asks=ordered([Level(price=x["price"], quantity=x["size"]) for x in data["asks"]]),
                exchange_at=timestamp,
                received_at=now(),
                transport="websocket",
                source=market.source,
            )
            self.books[token] = LocalBook(book)
            return [book]
        if kind == "price_change":
            changed = set()
            for change in data["price_changes"]:
                token = change["asset_id"]
                if token not in self.books:
                    raise BookIntegrityError("SNAPSHOT_REQUIRED")
                if change["side"] not in ("BUY", "SELL"):
                    raise BookIntegrityError("INVALID_DELTA_SIDE")
                local = self.books[token]
                local.delta(
                    "bids" if change["side"] == "BUY" else "asks",
                    D(change["price"]),
                    D(change["size"]),
                    timestamp,
                )
                local.book.received_at = now()
                local.book.processed_at = now()
                changed.add(token)
            return [self.books[token].book for token in changed]
        if kind in ("tick_size_change", "market_resolved"):
            raise BookIntegrityError("MARKET_LIFECYCLE_REDISCOVERY_REQUIRED")
        return []


async def international_stream(markets: list[Market]) -> AsyncIterator[list[Book]]:
    state = InternationalStreamState(markets)
    async with connect(
        "wss://ws-subscriptions-clob.polymarket.com/ws/market",
        open_timeout=15,
        ping_interval=10,
        ping_timeout=10,
        max_size=2_000_000,
    ) as socket:
        await socket.send(
            json.dumps(
                {
                    "assets_ids": list(state.tokens),
                    "type": "market",
                    "initial_dump": True,
                    "custom_feature_enabled": True,
                }
            )
        )

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(10)
                await socket.send("PING")

        task = asyncio.create_task(heartbeat())
        try:
            while True:
                raw = await asyncio.wait_for(socket.recv(), timeout=30)
                if raw == "PONG":
                    continue
                data = decode(str(raw))
                for item in data if isinstance(data, list) else [data]:
                    books = state.apply(item)
                    if books:
                        yield books
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def us_stream(settings: Settings, markets: list[Market]) -> AsyncIterator[list[Book]]:
    by_slug = {x.external_id: x for x in markets}
    async with connect(
        "wss://api.polymarket.us/v1/ws/markets",
        additional_headers=us_headers(settings),
        open_timeout=15,
        ping_interval=10,
        ping_timeout=10,
        max_size=2_000_000,
    ) as socket:
        for index in range(0, len(markets), 100):
            await socket.send(
                json.dumps(
                    {
                        "subscribe": {
                            "requestId": f"books-{index}",
                            "subscriptionType": "SUBSCRIPTION_TYPE_MARKET_DATA",
                            "marketSlugs": [x.external_id for x in markets[index : index + 100]],
                        },
                    }
                )
            )
        while True:
            data = decode(str(await asyncio.wait_for(socket.recv(), timeout=30)))
            if "marketData" in data:
                market = by_slug[data["marketData"]["marketSlug"]]
                yield us_books(market, USBook.model_validate(data), "websocket")

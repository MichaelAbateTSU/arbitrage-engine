from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from urllib.parse import quote

from app.config import Settings
from app.domain import Book, D, Level, Market, Venue, now
from app.matching import LEAGUES, Alias
from app.pricing import complementary_book, ordered
from app.venues.http import PublicHTTP, VenueError
from app.venues.mapper import gamma_market, kalshi_market, us_market
from app.venues.schemas import (
    GammaMarket,
    InternationalBook,
    KalshiBook,
    KalshiPage,
    USBook,
    USMarket,
    USPage,
)

KALSHI_SERIES = {
    "NFL": "KXNFLGAME",
    "NBA": "KXNBAGAME",
    "MLB": "KXMLBGAME",
    "NHL": "KXNHLGAME",
    "NCAAF": "KXNCAAFGAME",
    "NCAAB": "KXNCAAMBGAME",
}


class VenueClient(Protocol):
    venue: Venue

    def discover(self, aliases: list[Alias]) -> AsyncIterator[Market]: ...
    async def get_orderbooks(self, market: Market) -> list[Book]: ...
    async def get_settlement(self, market: Market) -> D | None: ...


class KalshiReadConfig(Protocol):
    @property
    def kalshi_environment(self) -> Literal["production", "demo"]: ...

    @property
    def btc_15m_enabled(self) -> bool: ...


class KalshiClient:
    venue = Venue.KALSHI

    def __init__(self, http: PublicHTTP, settings: KalshiReadConfig) -> None:
        self.http = http
        self.btc_enabled = settings.btc_15m_enabled
        self.fee_deadlines: dict[str, datetime] = {}
        self.base = (
            "https://external-api.kalshi.com/trade-api/v2"
            if settings.kalshi_environment == "production"
            else "https://external-api.demo.kalshi.co/trade-api/v2"
        )

    async def _event_rate(self, event_id: str, series: dict[str, Any]) -> D | None:
        self.fee_deadlines.pop(event_id, None)
        cursor = ""
        changes = []
        seen = set()
        while True:
            data = await self.http.get(
                f"{self.base}/events/fee_changes",
                {"event_ticker": event_id, "limit": 1000, "cursor": cursor},
            )
            changes.extend(data["event_fee_changes"])
            cursor = data["cursor"]
            if not cursor:
                break
            if cursor in seen:
                raise VenueError("PAGINATION_LOOP")
            seen.add(cursor)
        kind, multiplier = series["fee_type"], D(str(series["fee_multiplier"]))
        for change in sorted(changes, key=lambda x: x["scheduled_ts"]):
            effective = datetime.fromisoformat(change["scheduled_ts"].replace("Z", "+00:00"))
            if effective <= now():
                kind = change["fee_type_override"] or series["fee_type"]
                override = change["fee_multiplier_override"]
                multiplier = (
                    D(str(override)) if override is not None else D(str(series["fee_multiplier"]))
                )
            else:
                self.fee_deadlines[event_id] = min(
                    self.fee_deadlines.get(event_id, effective), effective
                )
        if kind not in (
            "quadratic",
            "quadratic_with_maker_fees",
            "quadratic_with_combo_maker_fees",
        ):
            return None
        return D("0.07") * multiplier

    async def discover(self, aliases: list[Alias]) -> AsyncIterator[Market]:
        series_universe = {**KALSHI_SERIES}
        if self.btc_enabled:
            series_universe["BTC"] = "KXBTC15M"
        for league, ticker in series_universe.items():
            async for market in self._discover_series(league, ticker, aliases):
                yield market

    async def _discover_series(
        self, league: str, ticker: str, aliases: list[Alias]
    ) -> AsyncIterator[Market]:
        series = (await self.http.get(f"{self.base}/series/{ticker}"))["series"]
        cursor = ""
        seen: set[str] = set()
        rates: dict[str, D | None] = {}
        while True:
            page = KalshiPage.model_validate(
                await self.http.get(
                    f"{self.base}/markets",
                    {
                        "series_ticker": ticker,
                        "status": "open",
                        "limit": 1000,
                        "cursor": cursor,
                        "mve_filter": "exclude",
                    },
                )
            )
            for wire in page.markets:
                if wire.event_ticker not in rates:
                    rates[wire.event_ticker] = await self._event_rate(wire.event_ticker, series)
                market = kalshi_market(wire, league, series, rates[wire.event_ticker], aliases)
                deadline = self.fee_deadlines.get(wire.event_ticker)
                if deadline and market.fee.valid_until:
                    market.fee.valid_until = min(market.fee.valid_until, deadline)
                yield market
            cursor = page.cursor
            if not cursor:
                return
            if cursor in seen:
                raise VenueError("PAGINATION_LOOP")
            seen.add(cursor)

    async def discover_bitcoin(self) -> AsyncIterator[Market]:
        async for market in self._discover_series("BTC", "KXBTC15M", []):
            yield market

    async def get_orderbooks(self, market: Market) -> list[Book]:
        if (
            market.market_type not in ("moneyline", "btc_up_down_15m")
            or market.external_id == "KXUS500PERP"
        ):
            raise VenueError("KALSHI_NON_BINARY_PRODUCT_UNSUPPORTED")
        if market.bitcoin and now() >= market.bitcoin.window_end:
            raise VenueError("BTC_WINDOW_EXPIRED")
        current = await self.http.get(f"{self.base}/markets/{quote(market.external_id, safe='')}")
        state = current["market"]
        if state["status"] != "active":
            raise VenueError("MARKET_NOT_TRADABLE")
        current_rules = (
            f"{state.get('rules_primary', '')}\n{state.get('rules_secondary', '')}"
        ).strip()
        if current_rules != market.rules_text or state["title"] != market.title:
            raise VenueError("MARKET_SPECIFICATION_CHANGED")
        if market.bitcoin and any(
            str(state.get(key)) != str(market.raw.get(key))
            for key in ("open_time", "close_time", "floor_strike", "strike_type")
        ):
            raise VenueError("MARKET_SPECIFICATION_CHANGED")
        requested_at = now()
        data = await self.http.get(
            f"{self.base}/markets/{quote(market.external_id, safe='')}/orderbook", {"depth": 0}
        )
        wire = KalshiBook.model_validate(data)
        yes = [Level(price=p, quantity=q) for p, q in wire.orderbook_fp["yes_dollars"] if q > 0]
        no = [Level(price=p, quantity=q) for p, q in wire.orderbook_fp["no_dollars"] if q > 0]
        return list(
            complementary_book(
                market.id,
                yes,
                no,
                received_at=now(),
                requested_at=requested_at,
                source=market.source,
            )
        )

    async def get_settlement(self, market: Market) -> D | None:
        data = await self.http.get(f"{self.base}/markets/{quote(market.external_id, safe='')}")
        wire = data["market"]
        if wire["ticker"] != market.external_id:
            raise VenueError("SETTLEMENT_IDENTITY_MISMATCH")
        if wire["status"] not in ("settled", "finalized"):
            return None
        value = wire.get("settlement_value_dollars")
        if value is None:
            value = (
                "1"
                if wire.get("result") == "yes"
                else ("0" if wire.get("result") == "no" else None)
            )
        if value is None:
            return None
        payout = D(str(value))
        if payout not in (D("0"), D("0.5"), D("1")):
            raise VenueError("SCALAR_SETTLEMENT_FEE_RECONCILIATION_REQUIRED")
        return payout


class InternationalClient:
    venue = Venue.INTERNATIONAL

    def __init__(self, http: PublicHTTP) -> None:
        self.http = http
        self.gamma = "https://gamma-api.polymarket.com"
        self.clob = "https://clob.polymarket.com"

    async def discover(self, aliases: list[Alias]) -> AsyncIterator[Market]:
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            params: dict[str, Any] = {
                "closed": "false",
                "limit": 100,
                "sports_market_types": "moneyline",
            }
            if cursor:
                params["after_cursor"] = cursor
            data = await self.http.get(
                f"{self.gamma}/markets/keyset",
                params,
            )
            if not isinstance(data, dict) or not isinstance(data.get("markets"), list):
                raise VenueError("INVALID_DISCOVERY_ENVELOPE")
            for raw in data["markets"]:
                if raw.get("sportsMarketType") != "moneyline":
                    continue
                market = gamma_market(GammaMarket.model_validate(raw), aliases)
                if market.league in LEAGUES:
                    yield market
            cursor = data.get("next_cursor")
            if not cursor:
                return
            if cursor in seen:
                raise VenueError("PAGINATION_LOOP")
            seen.add(cursor)

    async def get_orderbooks(self, market: Market) -> list[Book]:
        books = []
        if len(market.token_ids) != 2:
            raise VenueError("MISSING_TOKEN_IDS")
        for side, token in market.token_ids.items():
            requested_at = now()
            data = await self.http.get(f"{self.clob}/book", {"token_id": token})
            wire = InternationalBook.model_validate(data)
            if wire.asset_id != token:
                raise VenueError("BOOK_TOKEN_MISMATCH")
            if not market.price_ranges or market.price_ranges != [
                market.price_ranges[0].model_copy(update={"step": D(wire.tick_size)})
            ]:
                raise VenueError("TICK_CHANGED_REDISCOVERY_REQUIRED")
            books.append(
                Book(
                    market_id=market.id,
                    outcome=side,
                    bids=ordered(
                        [Level(price=D(x["price"]), quantity=D(x["size"])) for x in wire.bids], True
                    ),
                    asks=ordered(
                        [Level(price=D(x["price"]), quantity=D(x["size"])) for x in wire.asks]
                    ),
                    exchange_at=datetime.fromtimestamp(int(wire.timestamp) / 1000, UTC),
                    received_at=now(),
                    requested_at=requested_at,
                    source=market.source,
                )
            )
        return books

    async def get_settlement(self, market: Market) -> D | None:
        condition = market.raw.get("conditionId")
        if not condition:
            raise VenueError("MISSING_CONDITION_ID")
        data = await self.http.get(
            "https://data-api.polymarket.com/v2/resolutions", {"condition": condition}
        )
        rows = [row for row in data["data"] if row.get("condition_id") == condition]
        if not rows or rows[0]["status"] != "resolved":
            return None
        payouts = rows[0].get("payouts")
        if payouts is None:
            return None
        if (
            len(payouts) != 2
            or any(type(value) is not int or value < 0 for value in payouts)
            or sum(payouts) != 1_000_000
        ):
            raise VenueError("INVALID_SETTLEMENT_PAYOUT_VECTOR")
        return D(payouts[0]) / 1_000_000


class USClient:
    venue = Venue.US

    def __init__(self, http: PublicHTTP, settings: Settings | None = None) -> None:
        self.http = http
        self.btc_enabled = settings is not None and settings.btc_15m_enabled
        self.base = "https://gateway.polymarket.us"

    async def discover(self, aliases: list[Alias]) -> AsyncIterator[Market]:
        offset = 0
        seen = set()
        while True:
            data = await self.http.get(
                f"{self.base}/v1/markets",
                {
                    "active": "true",
                    "closed": "false",
                    "limit": 100,
                    "offset": offset,
                },
            )
            page = USPage.model_validate(data)
            ids = tuple(x.id for x in page.markets)
            if ids in seen and ids:
                raise VenueError("PAGINATION_LOOP")
            seen.add(ids)
            for wire in page.markets:
                if wire.assetPriceTerms and not self.btc_enabled:
                    continue
                market = us_market(wire, aliases)
                if (market.league in LEAGUES and market.market_type == "moneyline") or (
                    self.btc_enabled and market.bitcoin is not None
                ):
                    yield market
            if not page.markets:
                return
            offset += len(page.markets)

    async def discover_bitcoin(self) -> AsyncIterator[Market]:
        offset = 0
        seen: set[tuple[str, ...]] = set()
        while True:
            page = USPage.model_validate(
                await self.http.get(
                    f"{self.base}/v1/markets",
                    {
                        "categories": "crypto",
                        "active": "true",
                        "closed": "false",
                        "limit": 100,
                        "offset": offset,
                    },
                )
            )
            identifiers = tuple(m.id for m in page.markets)
            if identifiers and identifiers in seen:
                raise VenueError("PAGINATION_LOOP")
            seen.add(identifiers)
            for wire in page.markets:
                terms = wire.assetPriceTerms
                if (
                    terms
                    and terms.marketType == "ASSET_PRICE_MARKET_TYPE_UP_DOWN"
                    and terms.horizon == "15m"
                    and terms.asset.symbol.lower() == "btc"
                ):
                    yield us_market(wire, [])
            if not page.markets:
                return
            offset += len(page.markets)

    async def get_orderbooks(self, market: Market) -> list[Book]:
        if market.bitcoin:
            if now() >= market.bitcoin.window_end:
                raise VenueError("BTC_WINDOW_EXPIRED")
            current = await self.http.get(
                f"{self.base}/v1/market/slug/{quote(market.external_id, safe='')}"
            )
            observed = us_market(USMarket.model_validate(current["market"]), [])
            if observed.public_spec_hash != market.public_spec_hash:
                raise VenueError("MARKET_SPECIFICATION_CHANGED")
            if not observed.tradable:
                raise VenueError("MARKET_NOT_TRADABLE")
        requested_at = now()
        data = await self.http.get(
            f"{self.base}/v1/markets/{quote(market.external_id, safe='')}/book"
        )
        return us_books(market, USBook.model_validate(data), requested_at=requested_at)

    async def get_settlement(self, market: Market) -> D | None:
        try:
            data = await self.http.get(
                f"{self.base}/v1/markets/{quote(market.external_id, safe='')}/settlement"
            )
        except VenueError as exc:
            if exc.status == 404:  # Official endpoint: market absent or not settled.
                return None
            raise
        if data["slug"] != market.external_id:
            raise VenueError("SETTLEMENT_IDENTITY_MISMATCH")
        payout = D(str(data["settlement"]))
        if not payout.is_finite() or not D("0") <= payout <= D("1"):
            raise VenueError("INVALID_SETTLEMENT_PAYOUT")
        return payout


def us_books(
    market: Market,
    wire: USBook,
    transport: Literal["rest", "websocket", "demo"] = "rest",
    requested_at: datetime | None = None,
) -> list[Book]:
    data = wire.marketData
    if data.marketSlug != market.external_id:
        raise VenueError("BOOK_MARKET_MISMATCH")
    if any(x.px.currency != "USD" for x in data.bids + data.offers):
        raise VenueError("UNKNOWN_BOOK_CURRENCY")
    yes = ordered([Level(price=x.px.value, quantity=x.qty) for x in data.bids if x.qty > 0], True)
    no = ordered(
        [Level(price=1 - x.px.value, quantity=x.qty) for x in data.offers if x.qty > 0], True
    )
    return list(
        complementary_book(
            market.id,
            yes,
            no,
            exchange_at=data.transactTime,
            received_at=now(),
            requested_at=requested_at,
            connected=data.state == "MARKET_STATE_OPEN",
            transport=transport,
            source=market.source,
        )
    )

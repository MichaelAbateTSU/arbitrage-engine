"""GET-only cohort discovery and batch books; never enables collateral return."""

import hashlib
from dataclasses import dataclass
from decimal import InvalidOperation
from typing import Any, Literal

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.domain import Book, D, Level, Side, exact_decimal, now
from app.eligibility import decode_kalshi_scope
from app.pricing import complementary_book, ordered
from app.single_venue import TERMS_SHA256, TERMS_URL, ResearchMarket, parse_market
from app.venues.clients import KalshiClient
from app.venues.http import PublicHTTP, VenueError, decode
from app.venues.mapper import public_fee
from app.venues.streams import kalshi_headers

BASE = "https://external-api.kalshi.com/trade-api/v2"


def _object(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise VenueError(code)
    return value


@dataclass(frozen=True)
class PublicConfig:
    kalshi_environment: Literal["production", "demo"] = "production"
    btc_15m_enabled: bool = False


class SingleVenueReader:
    def __init__(self, network: PublicHTTP) -> None:
        self.network = network
        self.fees = KalshiClient(network, PublicConfig())

    async def verify_terms(self) -> None:
        try:
            response = await self.network.client.get(TERMS_URL, timeout=20)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise VenueError("TERMS_DOWNLOAD_FAILED") from exc
        if not response.is_success:
            raise VenueError("TERMS_HTTP_ERROR", response.status_code)
        if len(response.content) > 8_000_000:
            raise VenueError("TERMS_TOO_LARGE")
        if hashlib.sha256(response.content).hexdigest() != TERMS_SHA256:
            raise VenueError("TERMS_CHANGED_REVIEW_REQUIRED")

    async def discover(self, limit: int = 6) -> list[dict[str, Any]]:
        if not 5 <= limit <= 10:
            raise ValueError("COHORT_LIMIT_REQUIRES_5_TO_10")
        cohorts = []
        for family in ("KXBTCD", "KXBTC"):
            series = _object(
                _object(
                    await self.network.get(f"{BASE}/series/{family}"),
                    "INVALID_SERIES_METADATA",
                ).get("series"),
                "INVALID_SERIES_METADATA",
            )
            if series.get("contract_terms_url") != TERMS_URL:
                raise VenueError("TERMS_URL_CHANGED")
            cursor = ""
            seen = set()
            for _ in range(20):
                page = _object(
                    await self.network.get(
                        f"{BASE}/events",
                        {"series_ticker": family, "status": "open", "limit": 100, "cursor": cursor},
                    ),
                    "INVALID_EVENT_UNIVERSE",
                )
                events = page.get("events")
                if not isinstance(events, list):
                    raise VenueError("INVALID_EVENT_UNIVERSE")
                for value in events:
                    event = _object(value, "INVALID_EVENT_UNIVERSE")
                    if (
                        event.get("series_ticker") != family
                        or not isinstance(event.get("event_ticker"), str)
                        or "-" not in event["event_ticker"]
                    ):
                        raise VenueError("EVENT_FAMILY_MISMATCH")
                    cohorts.append({"event_ticker": event["event_ticker"], "series": series})
                cursor = page.get("cursor", "")
                if not isinstance(cursor, str):
                    raise VenueError("INVALID_EVENT_CURSOR")
                if not cursor:
                    break
                if cursor in seen:
                    raise VenueError("PAGINATION_LOOP")
                seen.add(cursor)
            else:
                raise VenueError("EVENT_UNIVERSE_TOO_LARGE")
        cohorts.sort(key=lambda value: value["event_ticker"].split("-", 1)[1])
        if len({value["event_ticker"] for value in cohorts}) != len(cohorts):
            raise VenueError("DUPLICATE_EVENT")
        return cohorts[:limit]

    async def cohort(
        self, specification: dict[str, Any]
    ) -> tuple[
        dict[str, Any], list[ResearchMarket], dict[tuple[str, Side], Book], list[dict[str, str]]
    ]:
        ticker, series = specification["event_ticker"], specification["series"]
        # Refresh the series, event and fee overrides; old price metadata never supplies a fill.
        series = _object(
            _object(
                await self.network.get(f"{BASE}/series/{series['ticker']}"),
                "INVALID_SERIES_METADATA",
            ).get("series"),
            "INVALID_SERIES_METADATA",
        )
        data = _object(
            await self.network.get(f"{BASE}/events/{ticker}", {"with_nested_markets": "true"}),
            "INVALID_EVENT_METADATA",
        )
        event = _object(data.get("event"), "INVALID_EVENT_METADATA")
        if event["event_ticker"] != ticker or event["series_ticker"] != series["ticker"]:
            raise VenueError("EVENT_IDENTITY_MISMATCH")
        raw_markets = event.get("markets", data.get("markets", []))
        if not isinstance(raw_markets, list) or not 1 <= len(raw_markets) <= 1000:
            raise VenueError("INVALID_COHORT_UNIVERSE")
        try:
            rate = await self.fees._event_rate(ticker, series)
        except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation) as exc:
            raise VenueError("INVALID_EVENT_FEE_METADATA") from exc
        if rate is None:
            raise VenueError("UNSUPPORTED_EVENT_FEE")
        spec = public_fee("kalshi_bound", rate, "Current series and effective event fee changes")
        deadline = self.fees.fee_deadlines.get(ticker)
        if deadline and spec.valid_until:
            spec.valid_until = min(spec.valid_until, deadline)
        markets = []
        errors = []
        for raw in raw_markets:
            if not isinstance(raw, dict):
                errors.append({"ticker": "unknown", "code": "INVALID_MARKET_METADATA"})
                continue
            try:
                markets.append(parse_market(raw, event, series, spec))
            except (VenueError, ValueError, KeyError, TypeError) as exc:
                errors.append(
                    {
                        "ticker": str(raw.get("ticker", "unknown")),
                        "code": exc.code
                        if isinstance(exc, VenueError)
                        else "INVALID_MARKET_METADATA",
                    }
                )
        if len({market.ticker for market in markets}) != len(markets):
            raise VenueError("DUPLICATE_INSTRUMENT")
        if not any(market.active and now() < market.measurement_end for market in markets):
            raise VenueError("COHORT_NO_SUPPORTED_OPEN_CONTRACTS")
        books = {}
        tickers = [
            market.ticker for market in markets if market.active and now() < market.measurement_end
        ]
        for offset in range(0, len(tickers), 100):
            requested = tickers[offset : offset + 100]
            requested_at = now()
            payload = _object(
                await self.network.get(f"{BASE}/markets/orderbooks", {"tickers": requested}),
                "INVALID_BATCH_BOOK_RESPONSE",
            )
            received_at = now()
            response_rows = payload.get("orderbooks")
            if not isinstance(response_rows, list) or any(
                not isinstance(row, dict) or not isinstance(row.get("ticker"), str)
                for row in response_rows
            ):
                raise VenueError("INVALID_BATCH_BOOK_RESPONSE")
            returned = [row["ticker"] for row in response_rows]
            if len(set(returned)) != len(returned) or set(returned) != set(requested):
                raise VenueError("BATCH_BOOK_IDENTITY_MISMATCH")
            for row in response_rows:
                try:
                    ladders = row["orderbook_fp"]
                    yes = self._levels(ladders["yes_dollars"])
                    no = self._levels(ladders["no_dollars"])
                    for book in complementary_book(
                        row["ticker"],
                        yes,
                        no,
                        requested_at=requested_at,
                        received_at=received_at,
                        source="public",
                    ):
                        books[(book.market_id, book.outcome)] = book
                except (ValidationError, ValueError, TypeError, KeyError) as exc:
                    raise VenueError("INVALID_BATCH_BOOK_DEPTH") from exc
        return event, markets, books, errors

    @staticmethod
    def _levels(values: Any) -> list[Level]:
        if not isinstance(values, list) or len(values) > 10000:
            raise ValueError("INVALID_SNAPSHOT_LEVELS")
        result = []
        for value in values:
            if not isinstance(value, list) or len(value) != 2:
                raise ValueError("INVALID_SNAPSHOT_LEVEL")
            price, size = exact_decimal(value[0]), exact_decimal(value[1])
            if not D("0") <= price <= 1 or size < 0:
                raise ValueError("INVALID_SNAPSHOT_AMOUNT")
            if size:
                result.append(Level(price=price, quantity=size))
        return ordered(result, True)


async def account_evidence(http: httpx.AsyncClient, settings: Settings) -> dict[str, Any]:
    result: dict[str, Any] = {
        "observed_at": now().isoformat(),
        "trading_scope": "unverified",
        "binding_status": "unverified",
        "account_order_permission": "unverified",
        "kyc": "unverified",
        "primary_crypto_netting": None,
        "orders_submitted": False,
        "settings_changed": False,
    }
    if settings.kalshi_environment != "production" or settings.kalshi_api_key is None:
        raise VenueError("PRODUCTION_ACCOUNT_EVIDENCE_REQUIRED")
    for endpoint in ("/api_keys", "/portfolio/subaccounts/netting"):
        path = "/trade-api/v2" + endpoint
        try:
            response = await http.get(
                "https://external-api.kalshi.com" + path,
                headers=kalshi_headers(settings, path),
                timeout=15,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise VenueError("ACCOUNT_EVIDENCE_TIMEOUT") from exc
        if not response.is_success:
            result["error_code"] = "ACCOUNT_EVIDENCE_HTTP_ERROR"
            result["http_status"] = response.status_code
            return result
        try:
            data = decode(response.text)
            if endpoint == "/api_keys":
                result.update(decode_kalshi_scope(data, settings.kalshi_api_key.get_secret_value()))
                expiry = result.pop("region_expiration_ts")
                result["location_attestation"] = (
                    "current"
                    if expiry is not None and expiry > now().timestamp()
                    else "expired"
                    if expiry is not None
                    else "unverified"
                )
            else:
                configs = [
                    row
                    for row in data["netting_configs"]
                    if row["subaccount_number"] == 0 and row["exchange_index"] == 2
                ]
                if len(configs) == 1 and type(configs[0]["enabled"]) is bool:
                    result["primary_crypto_netting"] = configs[0]["enabled"]
                else:
                    result["netting_evidence_error"] = "PRIMARY_CRYPTO_NETTING_UNAVAILABLE"
        except (ValueError, TypeError, KeyError) as exc:
            raise VenueError("INVALID_ACCOUNT_EVIDENCE") from exc
    return result

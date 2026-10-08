"""Kalshi's distinct perps namespace, GET-only and excluded from binary workers."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.domain import now
from app.us500 import (
    TICKER,
    AccountEvidence,
    FundingEstimate,
    FundingEvent,
    LinearLevel,
    MarginBook,
    MarginMarket,
    diagnose_us500,
    exact,
)
from app.venues.http import PublicHTTP, VenueError, decode
from app.venues.streams import kalshi_headers

BASE = "https://external-api.kalshi.com/trade-api/v2/margin"


class MarginResearchClient:
    def __init__(self, network: PublicHTTP) -> None:
        self.network = network

    async def sample(
        self, size: Decimal | str, account: AccountEvidence | None = None
    ) -> dict[str, Any]:
        data = await self.network.get(f"{BASE}/markets/{TICKER}")
        requested_at = now()
        depth = await self.network.get(f"{BASE}/markets/{TICKER}/orderbook", {"depth": 100})
        received_at = now()
        funding = await self.network.get(f"{BASE}/funding_rates/estimate", {"ticker": TICKER})
        history = await self.network.get(
            f"{BASE}/funding_rates/historical",
            {"ticker": TICKER, "start_ts": int((now() - timedelta(days=7)).timestamp())},
        )
        try:
            market = MarginMarket.model_validate(data["market"])
            ladders = depth["orderbook"]
            book = MarginBook(
                bids=self._levels(ladders["bids"], True),
                asks=self._levels(ladders["asks"], False),
                requested_at=requested_at,
                received_at=received_at,
            )
            estimate = FundingEstimate.model_validate(funding)
            if len(history["funding_rates"]) > 1000:
                raise VenueError("MARGIN_HISTORY_TOO_LARGE")
            events = [FundingEvent.model_validate(event) for event in history["funding_rates"]]
            return diagnose_us500(market, book, estimate, events, exact(size), now(), account)
        except (ValidationError, ValueError, KeyError, TypeError) as exc:
            raise VenueError("INVALID_US500_PROVIDER_DATA") from exc

    @staticmethod
    def _levels(values: Any, descending: bool) -> list[LinearLevel]:
        if not isinstance(values, list) or len(values) > 1000:
            raise ValueError("INVALID_MARGIN_LADDER")
        if any(not isinstance(pair, list) or len(pair) != 2 for pair in values):
            raise ValueError("INVALID_MARGIN_LEVEL")
        levels = [LinearLevel(price=price, quantity=size) for price, size in values]
        # The live REST response reverses the ordering described in the docs.
        return sorted(levels, key=lambda level: level.price, reverse=descending)


async def read_account_evidence(
    http: httpx.AsyncClient, settings: Settings, observed_at: datetime | None = None
) -> AccountEvidence:
    evidence = AccountEvidence(observed_at=observed_at or now())
    for endpoint in ("enabled", "fee_tiers"):
        if endpoint == "fee_tiers" and evidence.enabled is not True:
            break
        path = f"/trade-api/v2/margin/{endpoint}"
        try:
            response = await http.get(
                f"https://external-api.kalshi.com{path}",
                headers=kalshi_headers(settings, path),
                timeout=15,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise VenueError("MARGIN_ACCOUNT_EVIDENCE_TIMEOUT") from exc
        if endpoint == "enabled":
            evidence.enabled_http_status = response.status_code
        else:
            evidence.fees_http_status = response.status_code
        if not response.is_success:
            evidence.error_code = "MARGIN_ACCOUNT_EVIDENCE_HTTP_ERROR"
            break
        try:
            data = decode(response.text)
            if endpoint == "enabled":
                if type(data["enabled"]) is not bool:
                    raise ValueError("INVALID_ENABLED_STATUS")
                evidence.enabled = data["enabled"]
            elif TICKER in data["taker_fee_rates"]:
                evidence.taker_fee_rate = exact(data["taker_fee_rates"][TICKER])
            else:
                evidence.error_code = "MARGIN_ACCOUNT_FEE_MISSING"
        except (ValueError, KeyError, TypeError) as exc:
            raise VenueError("INVALID_MARGIN_ACCOUNT_EVIDENCE") from exc
    return evidence

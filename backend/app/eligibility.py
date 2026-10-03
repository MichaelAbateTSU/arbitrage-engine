"""Read-only account evidence is not proof of permission to submit an order."""

from datetime import datetime, timedelta
from typing import Any, Literal

import httpx
import structlog
from pydantic import Field, model_validator
from sqlalchemy import select

from app.db import EligibilityRow, HealthRow
from app.domain import D, Model, Venue, now
from app.store import Store, upsert
from app.venues.http import VenueError, decode
from app.venues.streams import kalshi_headers, us_headers

log = structlog.get_logger()
PRODUCTS = {
    Venue.KALSHI: {
        "name": "Kalshi production exchange",
        "market_api": "https://external-api.kalshi.com/trade-api/v2",
        "account_api": "https://external-api.kalshi.com/trade-api/v2",
        "currency": "USD",
        "documentation": "https://docs.kalshi.com/api-reference/portfolio/get-balance",
    },
    Venue.US: {
        "name": "Polymarket US (separate US product)",
        "market_api": "https://gateway.polymarket.us",
        "account_api": "https://api.polymarket.us",
        "currency": "USD",
        "documentation": "https://docs.polymarket.us/learn/trading/access-and-limits/trading-restrictions",
    },
    Venue.INTERNATIONAL: {
        "name": "Polymarket international Gamma/CLOB",
        "market_api": "https://clob.polymarket.com",
        "metadata_api": "https://gamma-api.polymarket.com",
        "account_api": "https://clob.polymarket.com",
        "currency": "USDC",
        "documentation": "https://docs.polymarket.com/api-reference/geoblock",
    },
}


class AccountAttestation(Model):
    jurisdiction_confirmed: bool = False
    kyc_confirmed: bool = False
    order_permission_confirmed: bool = False
    market_restrictions_reviewed: bool = False
    evidence: str = Field(default="", max_length=4000)
    expires_at: datetime

    @model_validator(mode="after")
    def evidence_required(self) -> "AccountAttestation":
        if self.expires_at.tzinfo is None:
            raise ValueError("Attestation expiry must be timezone aware")
        if (
            any(
                (
                    self.jurisdiction_confirmed,
                    self.kyc_confirmed,
                    self.order_permission_confirmed,
                    self.market_restrictions_reviewed,
                )
            )
            and not self.evidence.strip()
        ):
            raise ValueError("Account attestations require independent evidence")
        if not now() < self.expires_at <= now() + timedelta(days=30):
            raise ValueError("Attestations expire within 30 days")
        return self


class Eligibility(Model):
    venue: Venue
    product: dict[str, str]
    operator_country: str
    operator_region: str
    price_access: Literal["connected", "disconnected", "unknown"] = "unknown"
    account_read_access: Literal["verified", "unverified", "failed"] = "unverified"
    order_permission: Literal["unverified", "operator_attested", "restricted"] = "unverified"
    open_order_eligible: bool = False
    jurisdiction_status: Literal["unknown", "operator_attested", "close_only"] = "unknown"
    available_balance: D | None = Field(default=None, ge=0, allow_inf_nan=False)
    balance_observed_at: datetime | None = None
    reasons: list[str] = Field(default_factory=list)
    attestation: dict[str, Any] | None = None
    probe_error: str | None = None
    live_execution_available: Literal[False] = False


async def eligibility_status(store: Store) -> dict[Venue, Eligibility]:
    async with store.sessions() as session:
        evidence = {
            Venue(row.venue): row.payload
            for row in (
                await session.scalars(
                    select(EligibilityRow).where(EligibilityRow.source == store.source)
                )
            ).all()
        }
        health = {
            row.venue: row.payload
            for row in (
                await session.scalars(select(HealthRow).where(HealthRow.source == store.source))
            ).all()
        }
    result = {}
    for venue in Venue:
        payload = evidence.get(venue, {})
        probe = payload.get("probe", {})
        attestation = payload.get("attestation")
        item = Eligibility(
            venue=venue,
            product=PRODUCTS[venue],
            operator_country=store.settings.operator_country,
            operator_region=store.settings.operator_region,
        )
        feed = health.get(venue, {}).get("feed", "")
        item.price_access = (
            "connected"
            if feed
            in ("websocket", "synthetic_demo", "rest_polling_credentials_needed_for_websocket")
            else ("disconnected" if feed == "disconnected" else "unknown")
        )
        at = datetime.fromisoformat(probe["at"]) if probe.get("at") else None
        fresh = at is not None and 0 <= (now() - at).total_seconds() < 900
        item.account_read_access = (
            "verified"
            if fresh and probe.get("verified")
            else ("failed" if probe.get("error") else "unverified")
        )
        item.probe_error = probe.get("error")
        if fresh and probe.get("available_balance") is not None:
            item.available_balance = D(probe["available_balance"])
            item.balance_observed_at = at
        if venue == Venue.INTERNATIONAL and store.settings.operator_country.upper() == "US":
            item.jurisdiction_status = "close_only"
            item.order_permission = "restricted"
            item.reasons.append("VENUE_CLOSE_ONLY_US_OPERATOR")
        valid_attestation = bool(
            attestation and datetime.fromisoformat(attestation["expires_at"]) > now()
        )
        if valid_attestation and attestation:
            item.attestation = attestation
            if item.jurisdiction_status != "close_only" and attestation["jurisdiction_confirmed"]:
                item.jurisdiction_status = "operator_attested"
            if item.order_permission != "restricted" and attestation["order_permission_confirmed"]:
                item.order_permission = "operator_attested"
        for confirmed, reason in (
            (item.jurisdiction_status == "operator_attested", "JURISDICTION_UNVERIFIED"),
            (valid_attestation and attestation and attestation["kyc_confirmed"], "KYC_UNVERIFIED"),
            (item.order_permission == "operator_attested", "ORDER_PERMISSION_UNVERIFIED"),
            (
                valid_attestation and attestation and attestation["market_restrictions_reviewed"],
                "MARKET_SPECIFIC_ELIGIBILITY_UNVERIFIED",
            ),
            (item.account_read_access == "verified", "ACCOUNT_READ_ACCESS_UNVERIFIED"),
            (item.available_balance is not None, "AVAILABLE_BALANCE_UNVERIFIED"),
        ):
            if not confirmed:
                item.reasons.append(reason)
        item.open_order_eligible = not item.reasons
        result[venue] = item
    return result


def available_balance(venue: Venue, data: dict[str, Any]) -> D:
    if venue == Venue.KALSHI:
        if isinstance(data.get("balance_dollars"), str):
            value = D(data["balance_dollars"])
        elif type(data.get("balance")) is int:
            value = D(data["balance"]) / 100
        else:
            raise VenueError("INVALID_BALANCE_RESPONSE")
    else:
        balances = data.get("balances")
        if not isinstance(balances, list):
            raise VenueError("INVALID_BALANCE_RESPONSE")
        if any(not isinstance(row, dict) for row in balances):
            raise VenueError("INVALID_BALANCE_RESPONSE")
        rows = [row for row in balances if row.get("currency") == "USD"]
        if len(rows) != 1 or rows[0].get("buyingPower") is None:
            raise VenueError("USD_BUYING_POWER_UNAVAILABLE")
        if isinstance(rows[0]["buyingPower"], (float, bool)):
            raise VenueError("INVALID_BALANCE_RESPONSE")
        value = D(str(rows[0]["buyingPower"]))
    if not value.is_finite() or value < 0:
        raise VenueError("INVALID_BALANCE_RESPONSE")
    return value


async def refresh_account_evidence(store: Store, client: httpx.AsyncClient) -> None:
    settings = store.settings
    for venue in (Venue.KALSHI, Venue.US):
        probe: dict[str, Any] = {"at": now().isoformat(), "verified": False}
        try:
            if venue == Venue.KALSHI:
                path = "/trade-api/v2/portfolio/balance"
                headers = kalshi_headers(settings, path)
                host = (
                    "https://external-api.kalshi.com"
                    if settings.kalshi_environment == "production"
                    else "https://external-api.demo.kalshi.co"
                )
            else:
                path = "/v1/account/balances"
                headers = us_headers(settings, path)
                host = "https://api.polymarket.us"
            response = await client.get(host + path, headers=headers, timeout=15)
            if not response.is_success:
                raise VenueError(f"ACCOUNT_READ_HTTP_{response.status_code}")
            data = decode(response.text)
            if not isinstance(data, dict):
                raise VenueError("INVALID_BALANCE_RESPONSE")
            probe.update(
                verified=True,
                available_balance=str(available_balance(venue, data)),
                environment=settings.kalshi_environment if venue == Venue.KALSHI else "production",
            )
            if probe["environment"] != "production":
                probe.update(verified=False, error="DEMO_ACCOUNT_NOT_PRODUCTION")
        except (VenueError, ValueError, TypeError, httpx.HTTPError) as exc:
            code = exc.code if isinstance(exc, VenueError) else type(exc).__name__
            probe["error"] = code
            log.warning("account_read_probe_failed", venue=venue, error_code=code)
            await store.system_error("eligibility", code)
        async with store.sessions.begin() as session:
            identifier = f"{store.source}:{venue}"
            previous = await session.get(EligibilityRow, identifier, with_for_update=True)
            payload = {**(previous.payload if previous else {}), "probe": probe}
            await upsert(
                session,
                EligibilityRow,
                identifier,
                source=store.source,
                venue=venue,
                payload=payload,
            )

"""Bounded unauthenticated BTC paper diagnostics; never loads .env or sends orders."""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
from app.config import Settings
from app.domain import Exposure, RiskSettings, Venue, now
from app.eligibility import PRODUCTS, Eligibility
from app.matching import match_markets
from app.validation import diagnose
from app.venues.clients import KalshiClient, USClient
from app.venues.http import PublicHTTP, VenueError


async def sample(
    network: PublicHTTP, settings: Settings, max_contracts: int
) -> dict[str, Any]:
    kalshi, us = KalshiClient(network, settings), USClient(network, settings)
    first = [m async for m in kalshi.discover_bitcoin()]
    second = [m async for m in us.discover_bitcoin()]
    at = now()
    result: dict[str, Any] = {
        "sample_started_at": at.isoformat(),
        "source": "public",
        "orders_submitted": False,
        "price_access_is_not_order_permission": True,
        "inventory": {"kalshi": len(first), "polymarket_us": len(second)},
        "pairs": [],
    }
    risk = RiskSettings(supported_leagues=["BTC"], max_contracts=max_contracts)
    eligibility = {
        venue: Eligibility(
            venue=venue,
            product=PRODUCTS[venue],
            operator_country="US",
            operator_region="GA",
        )
        for venue in Venue
    }
    for a in first:
        for b in second:
            if a.bitcoin is None or b.bitcoin is None:
                continue
            if (
                a.bitcoin.window_start != b.bitcoin.window_start
                or a.bitcoin.window_end != b.bitcoin.window_end
                or at >= a.bitcoin.window_end
            ):
                continue
            match = match_markets(a, b)
            books = {}
            errors = {}
            for client, market in ((kalshi, a), (us, b)):
                try:
                    observed = await client.get_orderbooks(market)
                except VenueError as exc:
                    errors[str(market.venue)] = {
                        "code": exc.code,
                        "http_status": exc.status,
                    }
                else:
                    books.update(
                        {(book.market_id, book.outcome): book for book in observed}
                    )
            sampled_at = now()
            values = diagnose(
                match, a, b, books, risk, eligibility, sampled_at, Exposure()
            )
            result["pairs"].append(
                {
                    "instruments": [a.external_id, b.external_id],
                    "opening_references": [
                        str(m.bitcoin.opening_reference) for m in (a, b)
                    ],
                    "window_start": a.bitcoin.window_start.isoformat(),
                    "window_end": a.bitcoin.window_end.isoformat(),
                    "approval": match.status,
                    "book_errors": errors,
                    "book_sampled_at": sampled_at.isoformat(),
                    "directions": [
                        {
                            "kalshi": value.first_outcome,
                            "polymarket_us": value.second_outcome,
                            "first_ask": str(value.first_ask)
                            if value.first_ask is not None
                            else None,
                            "second_ask": str(value.second_ask)
                            if value.second_ask is not None
                            else None,
                            "available_quantity": str(value.available_quantity),
                            "book_age_ms": [
                                value.first_book_age_ms,
                                value.second_book_age_ms,
                            ],
                            "calculation": value.calculation.model_dump(mode="json")
                            if value.calculation
                            else None,
                            "reasons": value.reasons,
                            "execution_reasons": value.execution_reasons,
                            "shadow_qualified": value.shadow_qualified,
                            "executable_for_operator": value.executable_for_operator,
                        }
                        for value in values
                    ],
                    "settlement_proof": values[0].settlement_proof,
                    "fee_coefficients": [str(m.fee.rate) for m in (a, b)],
                }
            )
    result["sample_completed_at"] = now().isoformat()
    result["qualified_directions"] = sum(
        direction["executable_for_operator"]
        for pair in result["pairs"]
        for direction in pair["directions"]
    )
    return result


async def run(samples: int, interval: float, max_contracts: int) -> dict[str, Any]:
    if not 1 <= samples <= 60 or interval < 2 or not 1 <= max_contracts <= 1000:
        raise ValueError(
            "Probe requires 1-60 samples, interval >=2s and 1-1000 contracts"
        )
    settings = Settings(
        _env_file=None,
        environment="test",
        data_mode="public",
        trading_mode="paper",
        btc_15m_enabled=True,
        live_trading_enabled=False,
        kalshi_execution_enabled=False,
        polymarket_execution_enabled=False,
        global_kill_switch=True,
        kalshi_api_key=None,
        kalshi_private_key=None,
        polymarket_us_key_id=None,
        polymarket_us_secret_key=None,
        database_url="sqlite+aiosqlite:///:memory:",
    )
    observations = []
    async with httpx.AsyncClient(follow_redirects=False) as http:
        network = PublicHTTP(http, rate=5)
        for index in range(samples):
            async with asyncio.timeout(90):
                observations.append(await sample(network, settings, max_contracts))
            if index + 1 < samples:
                await asyncio.sleep(interval)
    return {
        "kind": "read_only_public_btc_diagnostics",
        "limitations": (
            "Conditional price math is not settlement proof, account permission, a fill, "
            "measured profit or continuous coverage. No private environment file is loaded."
        ),
        "observations": observations,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--max-contracts", type=int, default=100)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    evidence = asyncio.run(run(args.samples, args.interval, args.max_contracts))
    encoded = json.dumps(evidence, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    else:
        print(encoded)


if __name__ == "__main__":
    main()

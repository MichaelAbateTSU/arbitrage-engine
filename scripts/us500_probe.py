"""Bounded perps research. Default: public GETs only, no .env, positions or orders."""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
from app.config import Settings
from app.domain import D, now
from app.us500 import FILING_SHA256, FILING_URL, exact, stress_cases
from app.venues.http import PublicHTTP, VenueError
from app.venues.perpetuals import MarginResearchClient, read_account_evidence


async def run(
    samples: int, interval: float, size: str, account_evidence: bool = False
) -> dict[str, Any]:
    amount = exact(size)
    if (
        not 1 <= samples <= 10
        or not 2 <= interval <= 60
        or not D("1") <= amount <= D("1000000")
    ):
        raise ValueError("Requires 1-10 samples, 2-60s interval and quantity 1-1000000")
    observations = []
    account = None
    async with httpx.AsyncClient(follow_redirects=False) as http:
        if account_evidence:
            account = await read_account_evidence(http, Settings())
        client = MarginResearchClient(PublicHTTP(http))
        for index in range(samples):
            if index:
                await asyncio.sleep(interval)
            try:
                observations.append(await client.sample(amount, account))
            except VenueError as exc:
                observations.append(
                    {
                        "sampled_at": now().isoformat(),
                        "error_code": exc.code,
                        "http_status": exc.status,
                        "arbitrage_qualified": False,
                    }
                )
    last_price = next(
        (
            value["next_funding_estimate"].get("mark_price")
            for value in reversed(observations)
            if "next_funding_estimate" in value
            and value["next_funding_estimate"].get("mark_price") is not None
        ),
        None,
    )
    return {
        "completed_at": now().isoformat(),
        "scope": "bounded_us500_read_only_research",
        "filing_url": FILING_URL,
        "filing_sha256": FILING_SHA256,
        "filing_is_current_terms_approval": False,
        "orders_submitted": False,
        "account_changes_made": False,
        "account_evidence_requested": account_evidence,
        "samples": observations,
        "synthetic_stress": stress_cases(D(last_price), amount)
        if last_price is not None
        else [],
        "synthetic_stress_is_live_profit": False,
        "qualified_opportunities": 0,
        "net_live_profit": None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument(
        "--quantity", default="1000", help="API contracts, not index-point contracts"
    )
    parser.add_argument(
        "--account-evidence", action="store_true", help="GET enabled/fees using .env"
    )
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    result = asyncio.run(
        run(
            arguments.samples,
            arguments.interval,
            arguments.quantity,
            arguments.account_evidence,
        )
    )
    arguments.output.write_text(
        json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(arguments.output),
                "samples": len(result["samples"]),
                "failed_samples": sum(
                    "error_code" in sample for sample in result["samples"]
                ),
                "qualified_opportunities": 0,
                "orders_submitted": False,
            }
        )
    )
    if all("error_code" in sample for sample in result["samples"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

"""Bounded Kalshi-only BTC feasibility, no account changes, orders or scheduled work."""

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
from app.config import Settings
from app.domain import D, Side, now
from app.single_venue import (
    TERMS_SHA256,
    TERMS_URL,
    EpisodeTracker,
    payout_floor,
    quote_bundle,
    quote_pair,
    relationships,
    scenario_points,
)
from app.venues.http import PublicHTTP, VenueError
from app.venues.single_venue import SingleVenueReader, account_evidence


async def run(
    samples: int, interval: float, cohorts: int, max_quantity: int, read_account: bool
) -> dict[str, Any]:
    if (
        not 1 <= samples <= 20
        or not 2 <= interval <= 60
        or not 1 <= max_quantity <= 1000
    ):
        raise ValueError(
            "Requires 1-20 samples, 2-60s interval and quantity cap 1-1000"
        )
    if not 5 <= cohorts <= 10:
        raise ValueError("Requires 5-10 event cohorts")
    tracker = EpisodeTracker()
    observations = []
    evidence = None
    async with httpx.AsyncClient(follow_redirects=False) as http:
        reader = SingleVenueReader(PublicHTTP(http))
        await reader.verify_terms()
        if read_account:
            evidence = await account_evidence(http, Settings())
        universe = await reader.discover(cohorts)
        if len(universe) < 5:
            raise VenueError("INSUFFICIENT_OPEN_EVENT_COHORTS")
        for index in range(samples):
            if index:
                await asyncio.sleep(interval)
            for specification in universe:
                begin = now()
                try:
                    event, markets, books, metadata_errors = await reader.cohort(
                        specification
                    )
                except (VenueError, ValueError, TypeError, KeyError) as exc:
                    tracker.interrupt(specification["event_ticker"])
                    observations.append(
                        {
                            "event_ticker": specification["event_ticker"],
                            "sampled_at": now().isoformat(),
                            "error_code": exc.code
                            if isinstance(exc, VenueError)
                            else "INVALID_COHORT_DATA",
                            "http_status": exc.status
                            if isinstance(exc, VenueError)
                            else None,
                        }
                    )
                    continue
                at = now()
                reasons: Counter[str] = Counter()
                count = positive = threshold_positive = gross_positive = quoted = 0
                best: list[dict[str, Any]] = []
                for a, side_a, b, side_b in relationships(markets):
                    result = quote_pair(a, side_a, b, side_b, books, at, max_quantity)
                    count += 1
                    reasons.update(set(result["reasons"]))
                    quoted += result["best_conditional_size"] is not None
                    positive += result["economic_positive_before_additional_costs"]
                    threshold_positive += result[
                        "threshold_positive_before_additional_costs"
                    ]
                    gross_positive += (
                        D(result["gross_before_fees"]) > 0
                        if "gross_before_fees" in result
                        else False
                    )
                    tracker.observe([result], at)
                    if result["best_conditional_size"] is not None:
                        best.append(result)
                        best.sort(
                            key=lambda row: D(
                                row["best_conditional_net_before_additional_costs"]
                            ),
                            reverse=True,
                        )
                        del best[5:]
                alternative = None
                no_baskets = []
                if event["series_ticker"] == "KXBTC" and markets:
                    predicates = [market.predicate for market in markets]
                    floor, maximum, missing = payout_floor(
                        predicates, [Side.YES] * len(markets)
                    )
                    gaps = [
                        str(point)
                        for point in scenario_points(predicates)
                        if not any(
                            predicate.pays_yes(point) for predicate in predicates
                        )
                    ]
                    alternative = {
                        "strategy": "all_yes_range_basket",
                        "normal_floor_without_assumed_rounding": str(floor),
                        "normal_maximum": str(maximum),
                        "no_data_floor": str(missing),
                        "continuous_value_gap_examples": gaps[:5],
                        "classification": "proven_incompatible",
                        "unparsed_market_count": len(metadata_errors),
                        "exhaustiveness_verified": False,
                    }
                    purchasable = [
                        market
                        for market in markets
                        if (market.ticker, Side.NO) in books
                        and books[(market.ticker, Side.NO)].asks
                    ]
                    purchasable.sort(
                        key=lambda market: books[(market.ticker, Side.NO)].asks[0].price
                    )
                    for length in range(3, min(10, len(purchasable)) + 1):
                        no_baskets.append(
                            quote_bundle(
                                purchasable[:length],
                                [Side.NO] * length,
                                books,
                                at,
                                max_quantity,
                            )
                        )
                    tracker.observe(no_baskets, at)
                observations.append(
                    {
                        "event_ticker": event["event_ticker"],
                        "series_ticker": event["series_ticker"],
                        "sample_started_at": begin.isoformat(),
                        "snapshot_evaluated_at": at.isoformat(),
                        "completed_at": now().isoformat(),
                        "collateral_return_type": event.get("collateral_return_type"),
                        "netting_assumed": False,
                        "market_count": len(markets),
                        "monitored_market_count": len(books) // 2,
                        "metadata_errors": metadata_errors,
                        "provider_book_timestamps_available": False,
                        "atomic_batch_snapshot_guaranteed": False,
                        "conditional_relationship_count": count,
                        "priced_relationship_count": quoted,
                        "gross_positive_relationship_count": gross_positive,
                        "positive_before_unknown_costs_count": positive,
                        "passes_existing_profit_threshold_before_unknown_costs": threshold_positive,
                        "qualified_count": 0,
                        "rejection_counts": dict(reasons),
                        "best_conditional_quotes_not_trade_recommendations": best,
                        "alternative": alternative,
                        "multi_no_basket_alternatives": no_baskets,
                        "multi_no_subset_search": "3-10 lowest-top-ask legs; not exhaustive subset search",
                    }
                )
    successes = [row for row in observations if "error_code" not in row]
    return {
        "scope": "bounded_single_venue_btc_research",
        "terms_url": TERMS_URL,
        "terms_sha256": TERMS_SHA256,
        "source": "public",
        "rulebook_and_joint_review_floor_verified": False,
        "account": evidence,
        "cost_route": {
            "route": "Hypothetical prefunded USD, buy-and-hold, no transfers during an episode",
            "operator_route_verified": False,
            "funding": "unresolved acquisition/financing/opportunity cost",
            "conversion": "not applicable to USD/USD route only",
            "withdrawal": "not applicable during episode; later bank route unresolved",
            "settlement": "current separate fee verification unresolved",
            "rebalancing": "not applicable during episode; unwind execution charged separately",
            "network": "not applicable to USD ledger route only",
            "all_in_costs_verified": False,
        },
        "observations": observations,
        "observed_cohorts": len({row["event_ticker"] for row in successes}),
        "conditional_economic_episodes": tracker.closed + list(tracker.active.values()),
        "qualified_opportunities": 0,
        "qualified_shadow_trials": 0,
        "shadow_status": "not_run_no_settlement_and_permission_qualified_candidates",
        "realized_profit": None,
        "orders_submitted": False,
        "settings_changed": False,
        "decision": "no_go_for_execution_current_feasibility_dependencies_unproven",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--cohorts", type=int, default=6)
    parser.add_argument("--max-quantity", type=int, default=100)
    parser.add_argument("--account-evidence", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        result = asyncio.run(
            run(
                arguments.samples,
                arguments.interval,
                arguments.cohorts,
                arguments.max_quantity,
                arguments.account_evidence,
            )
        )
    except (VenueError, ValueError) as exc:
        result = {
            "error_code": exc.code
            if isinstance(exc, VenueError)
            else "INVALID_PROBE_INPUT",
            "http_status": exc.status if isinstance(exc, VenueError) else None,
            "qualified_opportunities": 0,
            "orders_submitted": False,
        }
    arguments.output.write_text(
        json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(arguments.output),
                "observed_cohorts": result.get("observed_cohorts", 0),
                "qualified_opportunities": 0,
                "qualified_shadow_trials": 0,
                "error_code": result.get("error_code"),
                "failed_observations": sum(
                    "error_code" in row for row in result.get("observations", [])
                ),
            }
        )
    )
    if result.get("error_code") or result.get("observed_cohorts", 0) < 5:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

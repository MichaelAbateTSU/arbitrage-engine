"""Exact Bitcoin-window matching; conditional prices never approve a settlement hedge."""

from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from typing import Any, Literal

from app.domain import BitcoinPolicy, D, Market, Match, Side, Venue, fingerprint

BTC_TYPE = "btc_up_down_15m"
BTC_PROOF_VERSION = "btc-window-scenarios-v1"


def brti_average(
    samples: list[tuple[datetime, Decimal]], at: datetime, policy: BitcoinPolicy
) -> Decimal:
    if (
        at.tzinfo is None
        or policy.sample_start_offset is None
        or policy.sample_end_offset is None
        or policy.rounding_mode is None
    ):
        raise ValueError("BTC_SAMPLING_OR_ROUNDING_UNVERIFIED")
    expected = {
        at + timedelta(seconds=offset)
        for offset in range(policy.sample_start_offset, policy.sample_end_offset + 1)
    }
    if len(samples) != 60 or {stamp for stamp, _ in samples} != expected:
        raise ValueError("BTC_INCOMPLETE_OR_WRONG_SAMPLE_INTERVAL")
    if any(
        type(value) is not Decimal or not value.is_finite() or value <= 0 for _, value in samples
    ):
        raise ValueError("BTC_INVALID_INDEX_VALUE")
    rounding = ROUND_HALF_EVEN if policy.rounding_mode == "half_even" else ROUND_HALF_UP
    return (sum((value for _, value in samples), D("0")) / 60).quantize(
        D("0.01"), rounding=rounding
    )


def bitcoin_event_identity(market: Market) -> str:
    window = market.bitcoin
    return fingerprint(
        ["BTC", "BRTI", window.window_start, window.window_end] if window else [market.id]
    )[:24]


def bitcoin_match(a: Market, b: Market, human_reviewed: bool = False) -> Match:
    conflicts: list[str] = []
    unknown: list[str] = []
    if a.venue != Venue.KALSHI or b.venue != Venue.US:
        conflicts.append("BTC_UNSUPPORTED_VENUE_PAIR")
    if a.source != b.source:
        conflicts.append("SOURCE_MODE_MISMATCH")
    if a.market_type != BTC_TYPE or b.market_type != BTC_TYPE:
        conflicts.append("MARKET_TYPE_MISMATCH")
    if a.bitcoin is None or b.bitcoin is None:
        unknown.append("BTC_WINDOW_IDENTITY_MISSING")
    else:
        one, two = a.bitcoin, b.bitcoin
        for name in ("asset", "benchmark", "window_start", "window_end"):
            if getattr(one, name) != getattr(two, name):
                conflicts.append(f"BTC_{name.upper()}_MISMATCH")
        if one.opening_reference is None or two.opening_reference is None:
            unknown.append("BTC_OPENING_REFERENCE_UNAVAILABLE")
        elif one.opening_reference != two.opening_reference:
            conflicts.append("BTC_OPENING_REFERENCE_MISMATCH")
        for name in (
            "sample_start_offset",
            "sample_end_offset",
            "rounding_mode",
            "revision_deadline_seconds",
        ):
            first, second = getattr(one.policy, name), getattr(two.policy, name)
            if first is None or second is None:
                unknown.append(f"BTC_{name.upper()}_UNVERIFIED")
            elif first != second:
                conflicts.append(f"BTC_{name.upper()}_MISMATCH")
        if one.policy.missing_data != two.policy.missing_data or one.policy.missing_data not in (
            "no",
            "half_refund",
        ):
            unknown.append("BTC_MISSING_DATA_PAYOUT_UNPROVEN")
        if not (
            one.policy.discretionary_settlement == two.policy.discretionary_settlement == "excluded"
        ):
            unknown.append("BTC_DISCRETIONARY_PAYOUT_UNPROVEN")
    if a.participants != ["up", "down"] or b.participants != ["up", "down"]:
        conflicts.append("BTC_OUTCOME_MAPPING_MISMATCH")
    if a.yes_team != "up" or b.yes_team != "up":
        conflicts.append("BTC_OUTCOME_MAPPING_MISMATCH")
    if a.rules.payout != 1 or b.rules.payout != 1:
        conflicts.append("PAYOUT_MISMATCH")
    if not a.rules_text.strip() or not b.rules_text.strip():
        unknown.append("MISSING_RULES")
    if a.source == "public" and not human_reviewed:
        unknown.append("RULE_TEXT_REVIEW_REQUIRED")
    status: Literal["approved", "review", "rejected"] = (
        "rejected" if conflicts else "review" if unknown else "approved"
    )
    return Match(
        id=fingerprint([a.id, b.id, a.rules_hash, b.rules_hash])[:32],
        event_id=bitcoin_event_identity(a),
        first_market_id=a.id,
        second_market_id=b.id,
        first_rules_hash=a.rules_hash,
        second_rules_hash=b.rules_hash,
        status=status,
        confidence=D("1") if status == "approved" else D("0"),
        reasons=list(dict.fromkeys(conflicts + unknown)),
        evidence={
            "bitcoin": [m.bitcoin.model_dump(mode="json") if m.bitcoin else None for m in (a, b)],
            "normal_condition": "Closing rounded BRTI average >= immutable opening reference.",
            "limitation": "Normal reference agreement alone does not bound exceptional payouts.",
        },
        human_reviewed=human_reviewed,
        version=BTC_PROOF_VERSION,
    )


def bitcoin_profile(market: Market) -> dict[str, Any]:
    window = market.bitcoin
    return {
        "venue": str(market.venue),
        "league": "BTC",
        "market_type": BTC_TYPE,
        "quote_currency": market.quote_currency,
        "asset": window.asset if window else None,
        "settlement_source": window.benchmark if window else None,
        "duration_seconds": 900,
        "policy": window.policy.model_dump(mode="json") if window else None,
        "fallback": "unverified_crypto_exception",
        "postponement": None,
        "payout": str(market.rules.payout),
    }


def bitcoin_proof(a: Market, b: Market) -> dict[str, Any]:
    match = bitcoin_match(a, b, human_reviewed=True)
    profiles = [bitcoin_profile(a), bitcoin_profile(b)]
    normal_reasons = [
        reason
        for reason in match.reasons
        if reason not in ("BTC_MISSING_DATA_PAYOUT_UNPROVEN", "BTC_DISCRETIONARY_PAYOUT_UNPROVEN")
    ]
    scenarios = []
    for side in Side:
        for scenario, covered in (
            ("close_above_open", not normal_reasons),
            ("close_below_open", not normal_reasons),
            ("close_equals_open_up_wins", not normal_reasons),
            (
                "missing_or_incomplete_index_data",
                not normal_reasons and "BTC_MISSING_DATA_PAYOUT_UNPROVEN" not in match.reasons,
            ),
            (
                "discretionary_review_or_cancellation",
                not normal_reasons and "BTC_DISCRETIONARY_PAYOUT_UNPROVEN" not in match.reasons,
            ),
        ):
            scenarios.append(
                {
                    "direction": side,
                    "scenario": scenario,
                    "combined_payout": "1" if covered else None,
                    "minimum_payout": "1" if covered else None,
                    "maximum_payout": "1" if covered else None,
                    "covered": covered,
                    "evidence": (
                        "Conditional on verified identical window, sample set, rounding, "
                        "revision cutoff and outcome mapping; exceptional rules are separate."
                        if scenario.startswith("close_")
                        else "Requires compatible governing terms, not a normal settlement example."
                    ),
                }
            )
    bounds = {
        str(side): {
            "minimum_combined_payout": "1"
            if all(row["covered"] for row in scenarios if row["direction"] == side)
            else None,
            "known_scenario_floor": "1"
            if any(row["covered"] for row in scenarios if row["direction"] == side)
            else None,
            "unbounded_scenarios": [
                row["scenario"]
                for row in scenarios
                if row["direction"] == side and not row["covered"]
            ],
        }
        for side in Side
    }
    return {
        "version": BTC_PROOF_VERSION,
        "proven": match.status == "approved" and all(row["covered"] for row in scenarios),
        "reasons": match.reasons,
        "scenarios": scenarios,
        "direction_bounds": bounds,
        "rules_hashes": [a.rules_hash, b.rules_hash],
        "profiles": profiles,
        "family_id": fingerprint([BTC_PROOF_VERSION, profiles])[:32],
        "comparison": match.evidence,
        "deadlines": {
            "window_start": [
                m.bitcoin.window_start.isoformat() if m.bitcoin else None for m in (a, b)
            ],
            "last_trading": [
                m.bitcoin.window_end.isoformat() if m.bitcoin else None for m in (a, b)
            ],
        },
        "evidence": [m.bitcoin.policy.evidence if m.bitcoin else {} for m in (a, b)],
        "limitation": "Unknown index/review outcomes remain unbounded. No real orders are routed.",
    }

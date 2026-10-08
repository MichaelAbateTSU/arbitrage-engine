"""Conservative payout bounds. A policy family never approves an individual contract."""

from typing import Any

from app.domain import D, Market, Side, fingerprint
from app.matching import match_markets, normalized

SCREEN_VERSION = "scenario-bounds-v1"


def rule_profile(market: Market) -> dict[str, Any]:
    if market.market_type == "btc_up_down_15m":
        from app.bitcoin import bitcoin_profile

        return bitcoin_profile(market)
    text = normalized(market.rules_text)
    windows = {
        "within_48h": ("within 48 hours",),
        "within_two_weeks": ("within two weeks", "within 14 days"),
        "within_two_calendar_days": ("within two calendar days",),
        "until_completed": (
            "remain open until the game has been completed",
            "postponed games remain open until completed",
        ),
    }
    detected = [
        name for name, phrases in windows.items() if any(phrase in text for phrase in phrases)
    ]
    window = detected[0] if len(detected) == 1 else market.rules.postponement
    fair_price = (
        any(phrase in text for phrase in ("fair price", "fair market price"))
        or market.rules.cancellation == "fair_price"
        or market.rules.discretionary_settlement == "independent_fair_price"
    )
    return {
        "venue": str(market.venue),
        "league": market.league,
        "market_type": market.market_type,
        "quote_currency": market.quote_currency,
        "period": market.rules.period,
        "overtime": market.rules.overtime,
        "draw": market.rules.draw,
        "cancellation": market.rules.cancellation,
        "postponement": window,
        "fallback": "independent_fair_price" if fair_price else "not_specified",
        "settlement_source": market.rules.settlement_source,
        "discretionary_settlement": market.rules.discretionary_settlement,
        "payout": str(market.rules.payout),
        "unclassified_text_hash": (
            fingerprint(market.rules_text) if window is None and not fair_price else None
        ),
    }


def settlement_proof(a: Market, b: Market) -> dict[str, Any]:
    if a.market_type == "btc_up_down_15m" or b.market_type == "btc_up_down_15m":
        from app.bitcoin import bitcoin_proof

        return bitcoin_proof(a, b)
    verified = match_markets(a, b, human_reviewed=True)
    profiles = [rule_profile(a), rule_profile(b)]
    scenarios: list[dict[str, Any]] = []
    normal_blockers = {
        "INVALID_VENUE_PAIR",
        "SOURCE_MODE_MISMATCH",
        "PARTICIPANTS_MISMATCH",
        "UNKNOWN_PARTICIPANTS",
        "UNKNOWN_OUTCOME",
        "OUTCOME_MISMATCH",
        "EVENT_TIME_MISMATCH",
        "UNKNOWN_EVENT_START",
        "SPORT_MISMATCH",
        "LEAGUE_MISMATCH",
        "MARKET_TYPE_MISMATCH",
        "UNKNOWN_SPORT",
        "UNKNOWN_LEAGUE",
        "UNKNOWN_MARKET_TYPE",
        "UNSUPPORTED_MARKET_TYPE",
        "OVERTIME_MISMATCH",
        "PERIOD_MISMATCH",
        "SETTLEMENT_SOURCE_MISMATCH",
        "UNKNOWN_PERIOD",
        "UNKNOWN_OVERTIME",
        "UNKNOWN_SETTLEMENT_SOURCE",
        "PAYOUT_MISMATCH",
    } & set(verified.reasons)

    def add(side: Side, name: str, minimum: D | None, maximum: D | None, evidence: str) -> None:
        scenarios.append(
            {
                "direction": side,
                "scenario": name,
                "combined_payout": str(minimum)
                if minimum == maximum and minimum is not None
                else None,
                "minimum_payout": str(minimum) if minimum is not None else None,
                "maximum_payout": str(maximum) if maximum is not None else None,
                "covered": minimum == maximum == 1,
                "evidence": evidence,
            }
        )

    for side in Side:
        other = side if verified.inverted else side.opposite
        identity_known = (
            len(a.participants) == 2
            and set(a.participants) == set(b.participants)
            and a.yes_team in a.participants
            and b.yes_team in b.participants
            and a.rules.payout == b.rules.payout == 1
        )
        for winner in a.participants:
            one, two = D(a.yes_team == winner), D(b.yes_team == winner)
            payout = (one if side == Side.YES else 1 - one) + (
                two if other == Side.YES else 1 - two
            )
            add(
                side,
                f"winner:{winner}",
                payout if identity_known else None,
                payout if identity_known else None,
                "Conditional on both venues recognizing the same official winner.",
            )
        if normal_blockers:
            add(
                side,
                "normal_completion_equivalence_unverified",
                None,
                None,
                "Conditional winner rows do not cover mismatched/unknown event identity, "
                "start, period, overtime or resolution authority: "
                + ", ".join(sorted(normal_blockers)),
            )
        draw = a.rules.draw
        if draw == b.rules.draw == "half_refund":
            add(side, "draw", D("1"), D("1"), "Both contracts explicitly refund half.")
        elif draw == b.rules.draw == "impossible":
            add(side, "draw", D("1"), D("1"), "Excluded by both verified rule profiles.")
        elif draw == b.rules.draw == "no":
            payout = D(side == Side.NO) + D(other == Side.NO)
            add(side, "draw", payout, payout, "Both YES contracts lose on a draw.")
        else:
            add(side, "draw", None, None, "Tie treatment has not been bounded.")
        refund = a.rules.cancellation == b.rules.cancellation == "half_refund"
        add(
            side,
            "cancelled_or_void",
            D("1") if refund else None,
            D("1") if refund else None,
            "Cancellation/void policies must be explicit; not inferred from postponement.",
        )
        same_completion = profiles[0]["postponement"] == profiles[1][
            "postponement"
        ] == "until_completed" and not any(
            profile["fallback"] == "independent_fair_price" for profile in profiles
        )
        for name in (
            "postponed_within_windows",
            "postponed_between_windows",
            "postponed_beyond_windows",
        ):
            if (
                name == "postponed_within_windows"
                and identity_known
                and all(profile["postponement"] is not None for profile in profiles)
            ):
                add(
                    side,
                    name,
                    D("1"),
                    D("1"),
                    "Conditional on starting within both venue windows "
                    "and recognizing the same final winner.",
                )
            elif same_completion:
                add(side, name, D("1"), D("1"), "Both stay open until the same game completes.")
            elif any(profile["fallback"] == "independent_fair_price" for profile in profiles):
                # Individual binary payouts are bounded by [0, 1]; no cross-venue
                # constraint justifies a tighter combined floor for independent fallback.
                add(
                    side,
                    name,
                    D("0"),
                    D("2"),
                    "Independent fair-price fallback has no verified "
                    "complementary payout constraint.",
                )
            else:
                add(side, name, None, None, "Postponement bounds are unknown.")
        if any(profile["fallback"] == "independent_fair_price" for profile in profiles):
            add(
                side,
                "discretionary_settlement",
                D("0"),
                D("2"),
                "Independent venue price decisions: conservative envelope, "
                "not a prediction of settlement.",
            )
        elif a.rules.discretionary_settlement == b.rules.discretionary_settlement == "excluded":
            add(
                side,
                "discretionary_settlement",
                D("1"),
                D("1"),
                "Both governing-terms reviews explicitly exclude discretionary payout exceptions.",
            )
        elif a.source == b.source == "demo":
            add(
                side,
                "discretionary_settlement",
                D("1"),
                D("1"),
                "Synthetic fixture has no discretionary exception.",
            )
        else:
            add(
                side,
                "discretionary_settlement",
                None,
                None,
                "Full governing terms and discretionary exceptions require independent evidence.",
            )

    bounds = {}
    for side in Side:
        values = [row for row in scenarios if row["direction"] == side]
        known = [D(row["minimum_payout"]) for row in values if row["minimum_payout"] is not None]
        bounds[str(side)] = {
            "minimum_combined_payout": (str(min(known)) if len(known) == len(values) else None),
            "known_scenario_floor": str(min(known)) if known else None,
            "unbounded_scenarios": [
                row["scenario"] for row in values if row["minimum_payout"] is None
            ],
        }
    return {
        "version": SCREEN_VERSION,
        "proven": verified.status == "approved" and all(row["covered"] for row in scenarios),
        "reasons": verified.reasons,
        "scenarios": scenarios,
        "direction_bounds": bounds,
        "rules_hashes": [a.rules_hash, b.rules_hash],
        "profiles": profiles,
        "family_id": fingerprint([SCREEN_VERSION, profiles])[:32],
        "comparison": {
            field: [
                str(value) if isinstance(value, D) else value
                for value in (getattr(a.rules, field), getattr(b.rules, field))
            ]
            for field in (
                "period",
                "overtime",
                "draw",
                "cancellation",
                "postponement",
                "settlement_source",
                "payout",
            )
        },
        "deadlines": {
            "event_start": [str(a.start_time), str(b.start_time)],
            "venue_close": [a.raw.get("close_time"), b.raw.get("endDate")],
            "postponement": [profile["postponement"] for profile in profiles],
        },
        "evidence": [a.rules.evidence, b.rules.evidence],
        "limitation": (
            "Unknown scenarios stay unproven. Conservative envelopes do not assert "
            "that their extrema will occur. Execution/one-leg failure risk is separate."
        ),
    }

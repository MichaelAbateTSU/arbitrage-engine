"""Delayed, persisted virtual execution and explicitly labeled hedge-failure stress cases."""

from datetime import datetime, timedelta
from decimal import ROUND_FLOOR
from typing import Any

from sqlalchemy import select

from app.arbitrage import calculate
from app.db import EpisodeRow, MatchRow, ShadowRow
from app.domain import (
    AdditionalCosts,
    Book,
    D,
    Level,
    Market,
    Match,
    PaperFill,
    PaperTrade,
    RiskSettings,
    Side,
    now,
)
from app.matching import match_markets
from app.paper import simulate_leg
from app.pricing import book_reasons, consume, cost, fee, quantity
from app.store import Store, audit
from app.validation import CandidateValidation, validation_settings


def extras(market: Market, risk: RiskSettings, filled: D) -> D:
    return (
        risk.additional_costs.get(market.venue, AdditionalCosts()).total(filled)
        if filled
        else D("0")
    )


def complete_trial(
    payload: dict[str, Any],
    a: Market,
    b: Market,
    books: dict[tuple[str, Side], Book],
    instant: datetime,
) -> tuple[str, dict[str, Any]]:
    signal = CandidateValidation.model_validate(payload["validation"])
    risk = RiskSettings.model_validate(payload["risk"])
    due = datetime.fromisoformat(payload["due_at"])
    original_a = Market.model_validate(signal.inputs["first_market"])
    original_b = Market.model_validate(signal.inputs["second_market"])
    if a.rules_hash != original_a.rules_hash or b.rules_hash != original_b.rules_hash:
        return "CANCELLED", {
            **payload,
            "failure_reason": "RULES_CHANGED",
            "closed_at": instant.isoformat(),
        }
    if not a.tradable or not b.tradable:
        return "CANCELLED", {
            **payload,
            "failure_reason": "MARKET_CLOSED",
            "closed_at": instant.isoformat(),
        }
    first = books.get((a.id, signal.first_outcome))
    second = books.get((b.id, signal.second_outcome))
    if (
        first is None
        or second is None
        or (
            min(first.requested_at or first.received_at, second.requested_at or second.received_at)
            < due
        )
    ):
        if instant > due + timedelta(milliseconds=risk.max_quote_age_ms):
            return "EXPIRED", {
                **payload,
                "failure_reason": "NO_POST_LATENCY_BOOK",
                "closed_at": instant.isoformat(),
            }
        return "SUBMITTED", payload
    reasons = book_reasons(first, a, instant, risk.max_quote_age_ms)
    reasons += book_reasons(second, b, instant, risk.max_quote_age_ms)
    if abs((first.received_at - second.received_at).total_seconds() * 1000) > risk.max_book_skew_ms:
        reasons.append("BOOK_TIME_SKEW")
    if not a.fee.known_at(instant) or not b.fee.known_at(instant):
        reasons.append("UNKNOWN_FEE")
    if reasons:
        return "EXPIRED", {
            **payload,
            "failure_reason": ",".join(sorted(set(reasons))),
            "closed_at": instant.isoformat(),
        }
    trial = PaperTrade(
        id=payload["id"],
        opportunity_id=signal.id,
        event_id=signal.event_id,
        league=signal.league,
        quantity=D(payload["quantity"]),
        decision_at=datetime.fromisoformat(payload["started_at"]),
        due_at=due,
        expected_profit=D(payload["expected_profit"]),
        reserved_capital=D(payload["reserved_capital"]),
        model="conservative",
        settings=risk,
        source=signal.source,
    )
    one = simulate_leg(first, a, trial, D(payload["limit_one"]))
    two = (
        PaperFill()
        if payload["scenario"] == "second_leg_rejected"
        else simulate_leg(second, b, trial, D(payload["limit_two"]))
    )
    if payload["scenario"] == "partial_second_leg":
        partial = trial.model_copy(
            update={
                "quantity": trial.quantity / 2,
            }
        )
        two = simulate_leg(second, b, partial, D(payload["limit_two"]))
    spent = (
        one.cost
        + two.cost
        + one.fee
        + two.fee
        + extras(a, risk, one.quantity)
        + extras(b, risk, two.quantity)
    )
    payload = {
        **payload,
        "first": one.model_dump(mode="json"),
        "second": two.model_dump(mode="json"),
        "fill_at": instant.isoformat(),
        "spent": str(spent),
        "execution_slippage": str((one.cost + two.cost) * risk.slippage_bps / 10000),
    }
    if one.quantity != two.quantity:
        return "UNWIND_PENDING", {
            **payload,
            "unwind_due_at": (
                instant + timedelta(milliseconds=payload["unwind_latency_ms"])
            ).isoformat(),
            "failure_reason": "SECOND_LEG_REJECTED_STRESS"
            if (payload["scenario"] == "second_leg_rejected")
            else "PARTIAL_OR_ONE_SIDED_FILL",
        }
    if one.quantity:
        slip = (one.cost + two.cost) * risk.slippage_bps / 10000
        return "HEDGED", {
            **payload,
            "net_pnl": str(one.quantity - spent - slip),
            "pnl_basis": "hypothetical_locked_payout_not_settled",
        }
    return "FAILED", {
        **payload,
        "failure_reason": "LIMIT_NOT_EXECUTABLE",
        "closed_at": instant.isoformat(),
    }


def unwind_trial(
    payload: dict[str, Any],
    a: Market,
    b: Market,
    books: dict[tuple[str, Side], Book],
    instant: datetime,
) -> tuple[str, dict[str, Any]]:
    signal = CandidateValidation.model_validate(payload["validation"])
    risk = RiskSettings.model_validate(payload["risk"])
    one, two = (
        PaperFill.model_validate(payload["first"]),
        PaperFill.model_validate(payload["second"]),
    )
    market, side = (
        (a, signal.first_outcome) if one.quantity > two.quantity else (b, signal.second_outcome)
    )
    excess = abs(one.quantity - two.quantity)
    book = books.get((market.id, side))
    due = datetime.fromisoformat(payload["unwind_due_at"])
    levels: list[Level]
    if instant < due:
        return "UNWIND_PENDING", payload
    if book is None or (book.requested_at or book.received_at) < due:
        if instant <= due + timedelta(milliseconds=risk.max_quote_age_ms):
            return "UNWIND_PENDING", payload
        levels = []
    elif book_reasons(book, market, instant, risk.max_quote_age_ms) or not market.fee.known_at(
        instant
    ):
        levels = []
    else:
        levels = consume(
            sorted(book.bids, key=lambda level: level.price, reverse=True),
            excess,
            haircut=risk.liquidity_haircut,
        )
        sellable = (quantity(levels) / market.quantity_step).to_integral_value(
            rounding=ROUND_FLOOR
        ) * market.quantity_step
        levels = consume(levels, sellable)
        if sellable < market.minimum_quantity or cost(levels) < market.minimum_notional:
            levels = []
    recovered = cost(levels)
    exit_fee = fee(levels, market.fee) if levels else D("0")
    residual = excess - quantity(levels)
    profit = min(one.quantity, two.quantity) + recovered - D(payload["spent"]) - exit_fee
    profit -= (one.cost + two.cost + recovered) * risk.slippage_bps / 10000
    held_pair = min(one.quantity, two.quantity) > 0
    state = "RESIDUAL_EXPOSURE" if residual else ("HEDGED_AFTER_UNWIND" if held_pair else "UNWOUND")
    result = {
        **payload,
        "unwind_proceeds": str(recovered),
        "unwind_fee": str(exit_fee),
        "unwind_levels": [level.model_dump(mode="json") for level in levels],
        "unwind_market_id": market.id,
        "unwind_quantity": str(quantity(levels)),
        "residual_quantity": str(residual),
        "net_pnl": str(profit),
        "execution_slippage": str((one.cost + two.cost + recovered) * risk.slippage_bps / 10000),
        "pnl_basis": (
            "worst_case_zero_payout_on_residual"
            if residual
            else (
                "hypothetical_hedged_after_unwind_not_settled" if held_pair else "simulated_unwind"
            )
        ),
        "unwind_at": instant.isoformat(),
    }
    if state == "UNWOUND":
        result["closed_at"] = instant.isoformat()
    return state, result


def settle_trial(
    payload: dict[str, Any], a: Market, b: Market, instant: datetime
) -> dict[str, Any]:
    signal = CandidateValidation.model_validate(payload["validation"])
    one, two = (
        PaperFill.model_validate(payload["first"]),
        PaperFill.model_validate(payload["second"]),
    )
    if a.result is None or b.result is None:
        raise ValueError("SHADOW_SETTLEMENT_UNAVAILABLE")
    sold = D(payload.get("unwind_quantity", "0"))
    q1 = one.quantity - (sold if payload.get("unwind_market_id") == a.id else D("0"))
    q2 = two.quantity - (sold if payload.get("unwind_market_id") == b.id else D("0"))
    p1 = a.result if signal.first_outcome == Side.YES else 1 - a.result
    p2 = b.result if signal.second_outcome == Side.YES else 1 - b.result
    profit = (
        q1 * p1
        + q2 * p2
        + D(payload.get("unwind_proceeds", "0"))
        - D(payload["spent"])
        - D(payload.get("unwind_fee", "0"))
        - D(payload.get("execution_slippage", "0"))
    )
    return {
        **payload,
        "net_pnl": str(profit),
        "pnl_basis": "hypothetical_observed_settlement",
        "closed_at": instant.isoformat(),
    }


async def shadow_tick(store: Store) -> None:
    config, _, _ = await validation_settings(store)
    markets = {market.id: market for market in await store.markets()}
    books = await store.books()
    instant = now()
    async with store.sessions.begin() as session:
        rows = (
            await session.scalars(select(ShadowRow).where(ShadowRow.source == store.source))
        ).all()
        for row in rows:
            payload = row.payload
            signal = CandidateValidation.model_validate(payload["validation"])
            a, b = markets.get(signal.first_market_id), markets.get(signal.second_market_id)
            if a is None or b is None:
                continue
            state = row.state
            if state == "SUBMITTED" and instant >= row.due_at.replace(tzinfo=instant.tzinfo):
                match_row = await session.get(MatchRow, signal.match_id, with_for_update=True)
                current = Match.model_validate(match_row.payload) if match_row else None
                if (
                    current is None
                    or current.status != "approved"
                    or (store.source == "public" and not current.human_reviewed)
                ):
                    state, payload = (
                        "CANCELLED",
                        {
                            **payload,
                            "failure_reason": "CURRENT_MATCH_APPROVAL_REQUIRED",
                            "closed_at": instant.isoformat(),
                        },
                    )
                else:
                    state, payload = complete_trial(payload, a, b, books, instant)
            elif state == "UNWIND_PENDING":
                state, payload = unwind_trial(payload, a, b, books, instant)
            elif state in ("HEDGED", "HEDGED_AFTER_UNWIND", "RESIDUAL_EXPOSURE") and (
                a.result is not None and b.result is not None
            ):
                state = "SETTLED"
                payload = settle_trial(payload, a, b, instant)
            if state != row.state:
                audit(
                    session,
                    store.source,
                    "shadow_transition",
                    "analysis",
                    {"id": row.id, "from": row.state, "to": state, "scenario": payload["scenario"]},
                )
                row.state, row.payload = state, payload
        if not config.enabled or not config.shadow_enabled:
            return
        baseline = [row for row in rows if row.payload["scenario"] == "baseline"]
        today = [
            row for row in baseline if row.payload["started_at"][:10] == instant.date().isoformat()
        ]
        loss = sum((max(D("0"), -D(row.payload.get("net_pnl", "0"))) for row in today), D("0"))
        if len(today) >= config.max_shadow_daily_trials or loss >= config.max_shadow_daily_loss:
            return
        existing = {row.id for row in rows}
        episodes = (
            await session.scalars(
                select(EpisodeRow).where(
                    EpisodeRow.source == store.source, EpisodeRow.active.is_(True)
                )
            )
        ).all()
        for episode in episodes:
            signal = CandidateValidation.model_validate(episode.payload["validation"])
            if config.selected_match_ids and signal.match_id not in config.selected_match_ids:
                continue
            if not signal.shadow_qualified or (instant - signal.at).total_seconds() > 15:
                continue
            if f"{episode.id}:baseline" in existing:
                continue
            match_row = await session.get(MatchRow, signal.match_id, with_for_update=True)
            current = Match.model_validate(match_row.payload) if match_row else None
            if (
                current is None
                or current.status != "approved"
                or (store.source == "public" and not current.human_reviewed)
            ):
                continue
            a, b = markets.get(signal.first_market_id), markets.get(signal.second_market_id)
            first = books.get((signal.first_market_id, signal.first_outcome))
            second = books.get((signal.second_market_id, signal.second_outcome))
            if a is None or b is None or first is None or second is None:
                continue
            risk = RiskSettings.model_validate(signal.inputs["risk"])
            risk = risk.model_copy(
                update={
                    "max_per_venue": min(risk.max_per_venue, config.max_shadow_per_leg),
                    "max_total_notional": min(
                        risk.max_total_notional, config.max_shadow_per_leg * 2
                    ),
                }
            )
            if (
                book_reasons(first, a, instant, risk.max_quote_age_ms)
                or book_reasons(second, b, instant, risk.max_quote_age_ms)
                or not a.fee.known_at(instant)
                or not b.fee.known_at(instant)
                or match_markets(a, b, signal.human_reviewed).status != "approved"
                or a.rules_hash != Market.model_validate(signal.inputs["first_market"]).rules_hash
                or b.rules_hash != Market.model_validate(signal.inputs["second_market"]).rules_hash
                or abs((first.received_at - second.received_at).total_seconds() * 1000)
                > risk.max_book_skew_ms
            ):
                continue
            calculation = calculate(first, second, a, b, risk)
            if calculation is None or (
                calculation.net_profit < risk.min_profit
                or calculation.net_profit <= 0
                or calculation.net_return < risk.min_edge
            ):
                continue
            capital = (
                calculation.cost_one
                + calculation.cost_two
                + calculation.fee_one
                + calculation.fee_two
                + calculation.additional_cost_one
                + calculation.additional_cost_two
                + calculation.slippage
                + calculation.safety_buffer
            )
            unavailable = {}
            for venue in (a.venue, b.venue):
                relevant = [
                    row
                    for row in baseline
                    if venue == a.venue or venue == row.payload["validation"]["second_venue"]
                ]
                held = sum(
                    (
                        D(row.payload["reserved_capital"])
                        for row in relevant
                        if row.state
                        in (
                            "SUBMITTED",
                            "UNWIND_PENDING",
                            "HEDGED",
                            "HEDGED_AFTER_UNWIND",
                            "RESIDUAL_EXPOSURE",
                        )
                    ),
                    D("0"),
                )
                realized_loss = sum(
                    (
                        max(D("0"), -D(row.payload.get("net_pnl", "0")))
                        for row in relevant
                        if row.state in ("SETTLED", "UNWOUND")
                    ),
                    D("0"),
                )
                # Reserve the combined cap on each venue; do not spend projected payouts.
                unavailable[venue] = held + realized_loss
            if any(
                held + capital > config.virtual_balance_per_venue for held in unavailable.values()
            ):
                continue
            for scenario in ("baseline", "second_leg_rejected", "partial_second_leg"):
                identifier = f"{episode.id}:{scenario}"
                due = instant + timedelta(milliseconds=risk.latency_ms)
                row = ShadowRow(
                    id=identifier,
                    source=store.source,
                    episode_id=episode.id,
                    state="SUBMITTED",
                    due_at=due,
                    payload={
                        "id": identifier,
                        "scenario": scenario,
                        "started_at": instant.isoformat(),
                        "due_at": due.isoformat(),
                        "quantity": str(calculation.quantity),
                        "reserved_capital": str(capital),
                        "expected_profit": str(calculation.net_profit),
                        "limit_one": str(calculation.limit_one),
                        "limit_two": str(calculation.limit_two),
                        "risk": risk.model_dump(mode="json"),
                        "validation": signal.model_dump(mode="json"),
                        "net_pnl": "0",
                        "unwind_latency_ms": config.unwind_latency_ms,
                        "label": "HYPOTHETICAL SHADOW; injected stress, not observed rejection.",
                    },
                )
                session.add(row)
                if scenario == "baseline":
                    baseline.append(row)
                    today.append(row)
            if len(today) >= config.max_shadow_daily_trials:
                break

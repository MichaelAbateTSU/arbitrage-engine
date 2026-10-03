from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, Decimal

from app.domain import (
    AdditionalCosts,
    Book,
    Calculation,
    D,
    Exposure,
    Market,
    Match,
    Opportunity,
    RiskSettings,
    Side,
    fingerprint,
)
from app.matching import match_markets
from app.pricing import book_reasons, consume, cost, fee, quantity
from app.settlement import settlement_proof


def calculate(
    first: Book, second: Book, a: Market, b: Market, settings: RiskSettings
) -> Calculation | None:
    if not first.asks or not second.asks:
        return None
    step = max(a.quantity_step, b.quantity_step)
    if step % a.quantity_step or step % b.quantity_step:
        raise ValueError("QUANTITY_ROUNDING_MISMATCH")
    available = min(quantity(first.asks), quantity(second.asks), D(settings.max_contracts))
    if settings.sizing_mode == "fixed_quantity":
        available = min(available, settings.fixed_quantity)
    per_venue = settings.max_per_venue
    total_limit = settings.max_total_notional
    if settings.sizing_mode == "bankroll":
        total_limit = min(total_limit, settings.bankroll * settings.bankroll_fraction)
    minimum = max(a.minimum_quantity, b.minimum_quantity, step)
    start = int((minimum / step).to_integral_value(rounding="ROUND_CEILING"))
    end = int((available / step).to_integral_value(rounding=ROUND_FLOOR))
    best: Calculation | None = None
    fallback: Calculation | None = None
    for units in range(start, end + 1):
        q = step * units
        one, two = consume(first.asks, q), consume(second.asks, q)
        c1, c2 = cost(one), cost(two)
        f1, f2 = fee(one, a.fee), fee(two, b.fee)
        slip = (c1 + c2) * settings.slippage_bps / 10000
        buffer = (c1 + c2) * settings.latency_buffer_bps / 10000
        extra_one = settings.additional_costs.get(a.venue, AdditionalCosts()).total(q)
        extra_two = settings.additional_costs.get(b.venue, AdditionalCosts()).total(q)
        capital = c1 + c2 + f1 + f2 + slip + buffer + extra_one + extra_two
        if c1 + f1 + slip / 2 + buffer / 2 + extra_one > per_venue:
            break
        if c2 + f2 + slip / 2 + buffer / 2 + extra_two > per_venue or capital > total_limit:
            break
        if c1 < a.minimum_notional or c2 < b.minimum_notional or c1 + c2 == 0:
            continue
        profit = q - capital
        result = Calculation(
            quantity=q,
            cost_one=c1,
            cost_two=c2,
            price_one=c1 / q,
            price_two=c2 / q,
            limit_one=one[-1].price,
            limit_two=two[-1].price,
            fee_one=f1,
            fee_two=f2,
            slippage=slip,
            safety_buffer=buffer,
            additional_cost_one=extra_one,
            additional_cost_two=extra_two,
            payout=q,
            gross_profit=q - c1 - c2,
            net_profit=profit,
            net_return=profit / (c1 + c2),
            unused_one=per_venue - c1 - f1 - slip / 2 - buffer / 2 - extra_one,
            unused_two=per_venue - c2 - f2 - slip / 2 - buffer / 2 - extra_two,
            binding_constraint="DEPTH_OR_CAPITAL",
            consumed_one=one,
            consumed_two=two,
        )
        fallback = result
        if profit >= settings.min_profit and result.net_return >= settings.min_edge and profit > 0:
            best = result
            if settings.sizing_mode == "target_profit" and profit >= settings.target_profit:
                result.binding_constraint = "TARGET_PROFIT"
                return result
    if best:
        if best.quantity == settings.max_contracts:
            best.binding_constraint = "MAX_CONTRACTS"
        elif best.quantity < available:
            best.binding_constraint = "CAPITAL_OR_NET_EDGE"
        else:
            best.binding_constraint = "AVAILABLE_DEPTH"
    return best or fallback


def exposure_reasons(settings: RiskSettings, exposure: Exposure, capital: Decimal) -> list[str]:
    reasons = []
    for exceeds, reason in (
        (
            exposure.committed + capital > settings.bankroll + exposure.settled_profit,
            "INSUFFICIENT_BANKROLL",
        ),
        (exposure.daily_capital + capital > settings.max_daily_capital, "DAILY_RISK_LIMIT_REACHED"),
        (
            exposure.event_capital + capital > settings.max_event_exposure,
            "EVENT_RISK_LIMIT_REACHED",
        ),
        (
            exposure.league_capital + capital > settings.max_league_exposure,
            "LEAGUE_RISK_LIMIT_REACHED",
        ),
        (exposure.open_trades >= settings.max_open_trades, "OPEN_TRADE_LIMIT_REACHED"),
        (
            settings.max_daily_trades > 0 and exposure.daily_trades >= settings.max_daily_trades,
            "DAILY_TRADE_LIMIT_REACHED",
        ),
        (exposure.daily_loss >= settings.max_daily_loss, "DAILY_LOSS_LIMIT_REACHED"),
        (
            exposure.pending_executions >= settings.max_simultaneous_executions,
            "SIMULTANEOUS_EXECUTION_LIMIT",
        ),
        (exposure.unhedged_quantity > settings.max_unhedged_quantity, "UNHEDGED_EXPOSURE_LIMIT"),
    ):
        if exceeds:
            reasons.append(reason)
    return reasons


def detect(
    match: Match,
    a: Market,
    b: Market,
    books: dict[tuple[str, Side], Book],
    settings: RiskSettings,
    instant: datetime,
    exposure: Exposure | None = None,
) -> tuple[list[Opportunity], list[str]]:
    failures: list[str] = []
    if a.quote_currency != b.quote_currency and not settings.allow_usdc_parity_assumption:
        failures.append("CURRENCY_ASSUMPTION_UNACKNOWLEDGED")
    current = match_markets(a, b, match.human_reviewed)
    if (
        match.status != "approved"
        or current.status != "approved"
        or match.first_rules_hash != a.rules_hash
        or match.second_rules_hash != b.rules_hash
    ):
        failures.append("MARKET_MATCH_UNAPPROVED")
    if settings.require_human_review and not match.human_reviewed:
        failures.append("HUMAN_REVIEW_REQUIRED")
    if not settlement_proof(a, b)["proven"]:
        failures.append("SETTLEMENT_SCENARIO_COVERAGE_UNPROVEN")
    if match.confidence < settings.min_confidence:
        failures.append("MATCH_CONFIDENCE_TOO_LOW")
    if a.status != "open" or b.status != "open" or not a.tradable or not b.tradable:
        failures.append("MARKET_NOT_TRADABLE")
    if a.league not in settings.supported_leagues:
        failures.append("SPORT_DISABLED")
    if a.id in settings.blocked_markets or b.id in settings.blocked_markets:
        failures.append("MARKET_BLOCKLISTED")
    if a.venue in settings.venue_kill_switches or b.venue in settings.venue_kill_switches:
        failures.append("VENUE_KILL_SWITCH")
    if not a.fee.known_at(instant) or not b.fee.known_at(instant):
        failures.append("UNKNOWN_FEE")
    if a.source == "public" and any(
        not settings.additional_costs.get(venue, AdditionalCosts()).known_at(instant)
        for venue in (a.venue, b.venue)
    ):
        failures.append("ADDITIONAL_COSTS_UNVERIFIED")
    if any(m.instrument_mapping_error() for m in (a, b)):
        failures.append("INSTRUMENT_MAPPING_INVALID")
    if exposure and exposure.cooldown_until and instant < exposure.cooldown_until:
        failures.append("EXECUTION_COOLDOWN")
    opportunities: list[Opportunity] = []
    if failures:
        return opportunities, failures
    for side in Side:
        other = side if match.inverted else side.opposite
        first, second = books.get((a.id, side)), books.get((b.id, other))
        if first is None or second is None:
            failures.append("MISSING_BOOK")
            continue
        reasons = book_reasons(first, a, instant, settings.max_quote_age_ms)
        reasons += book_reasons(second, b, instant, settings.max_quote_age_ms)
        if abs((first.received_at - second.received_at).total_seconds() * 1000) > (
            settings.max_book_skew_ms
        ):
            reasons.append("BOOK_TIME_SKEW")
        if reasons:
            failures += reasons
            continue
        if settings.kill_switch:
            reasons.append("KILL_SWITCH_ACTIVE")
        result = calculate(first, second, a, b, settings)
        if result is None:
            failures.append("INSUFFICIENT_DEPTH")
            continue
        if result.net_profit <= 0 or result.net_profit < settings.min_profit:
            reasons.append("BELOW_MINIMUM_DOLLAR_PROFIT")
        if result.net_return < settings.min_edge:
            reasons.append("BELOW_MINIMUM_NET_EDGE")
        if settings.sizing_mode == "target_profit" and result.net_profit < settings.target_profit:
            reasons.append("TARGET_PROFIT_UNAVAILABLE")
        capital = (
            result.cost_one
            + result.cost_two
            + result.fee_one
            + result.fee_two
            + result.slippage
            + result.safety_buffer
            + result.additional_cost_one
            + result.additional_cost_two
        )
        if exposure:
            reasons += exposure_reasons(settings, exposure, capital)
        inputs = {
            "first_book": first.model_dump(mode="json"),
            "second_book": second.model_dump(mode="json"),
            "first_market": a.model_dump(mode="json"),
            "second_market": b.model_dump(mode="json"),
            "match": match.model_dump(mode="json"),
            "risk": settings.model_dump(mode="json"),
        }
        opportunities.append(
            Opportunity(
                id=fingerprint([match.id, side, first.model_dump(), second.model_dump()])[:32],
                match_id=match.id,
                event_id=match.event_id,
                event=" vs ".join(a.participants),
                sport=a.sport,
                league=a.league,
                first_market_id=a.id,
                second_market_id=b.id,
                first_outcome=side,
                second_outcome=other,
                second_venue=b.venue,
                detected_at=instant,
                expires_at=min(
                    first.received_at,
                    second.received_at,
                    first.requested_at or first.received_at,
                    second.requested_at or second.received_at,
                    first.exchange_at or first.received_at,
                    second.exchange_at or second.received_at,
                )
                + timedelta(milliseconds=settings.max_quote_age_ms),
                calculation=result,
                confidence=match.confidence,
                quote_age_ms=max(first.age_ms(instant), second.age_ms(instant)),
                risk_status="rejected" if reasons else "qualified",
                reasons=list(dict.fromkeys(reasons)),
                inputs=inputs,
                source=a.source,
            )
        )
    return opportunities, list(dict.fromkeys(failures))

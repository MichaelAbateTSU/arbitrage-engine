from datetime import datetime
from decimal import ROUND_FLOOR

from app.domain import (
    AdditionalCosts,
    Book,
    D,
    Market,
    Opportunity,
    PaperFill,
    PaperState,
    PaperTrade,
    Side,
)
from app.pricing import book_reasons, consume, cost, fee, quantity

TERMINAL = {
    PaperState.HEDGED,
    PaperState.UNHEDGED,
    PaperState.FAILED,
    PaperState.EXPIRED,
    PaperState.CANCELLED,
    PaperState.SETTLED,
}
ALLOWED = {
    PaperState.CREATED: {PaperState.READY, PaperState.FAILED},
    PaperState.READY: {PaperState.SUBMITTED, PaperState.CANCELLED, PaperState.EXPIRED},
    PaperState.SUBMITTED: {
        PaperState.HEDGED,
        PaperState.UNHEDGED,
        PaperState.FAILED,
        PaperState.EXPIRED,
        PaperState.CANCELLED,
    },
    PaperState.HEDGED: {PaperState.SETTLED},
    PaperState.UNHEDGED: {PaperState.SETTLED},
}


def transition(trade: PaperTrade, state: PaperState, instant: datetime) -> None:
    if state not in ALLOWED.get(trade.state, set()):
        raise ValueError(f"INVALID_PAPER_TRANSITION:{trade.state}:{state}")
    trade.transitions.append({"from": trade.state, "to": state, "at": instant.isoformat()})
    trade.state = state


def simulate_leg(book: Book, market: Market, trade: PaperTrade, limit: D) -> PaperFill:
    haircut = trade.settings.liquidity_haircut if trade.model != "optimistic" else D("1")
    levels = consume(book.asks, trade.quantity, limit, haircut)
    q = (quantity(levels) / market.quantity_step).to_integral_value(rounding=ROUND_FLOOR)
    q *= market.quantity_step
    if q < market.minimum_quantity or q <= 0:
        return PaperFill()
    levels = consume(levels, q)
    value = cost(levels)
    if value < market.minimum_notional:
        return PaperFill()
    fees = fee(levels, market.fee)
    extra = trade.settings.additional_costs.get(market.venue, AdditionalCosts()).total(q)
    if value + fees + extra > trade.settings.max_per_venue:
        return PaperFill()
    return PaperFill(quantity=q, cost=value, fee=fees, price=value / q, consumed=levels)


def evaluate_paper(
    trade: PaperTrade,
    opportunity: Opportunity,
    books: dict[tuple[str, Side], Book],
    a: Market,
    b: Market,
    instant: datetime,
    kill_switch: bool,
) -> bool:
    if trade.state in TERMINAL or instant < trade.due_at:
        return False
    if kill_switch:
        transition(trade, PaperState.CANCELLED, instant)
        trade.failure_reason = "KILL_SWITCH_ACTIVE"
        return True
    from app.domain import Match

    match = Match.model_validate(opportunity.inputs["match"])
    if (
        a.rules_hash != match.first_rules_hash
        or b.rules_hash != match.second_rules_hash
        or a.status != "open"
        or b.status != "open"
        or not a.tradable
        or not b.tradable
    ):
        transition(trade, PaperState.CANCELLED, instant)
        trade.failure_reason = "MARKET_SPECIFICATION_CHANGED"
        return True
    first: Book | None
    second: Book | None
    if trade.model == "optimistic":
        first = Book.model_validate(opportunity.inputs["first_book"])
        second = Book.model_validate(opportunity.inputs["second_book"])
    else:
        first = books.get((a.id, opportunity.first_outcome))
        second = books.get((b.id, opportunity.second_outcome))
    if first is None or second is None:
        if instant > opportunity.expires_at:
            transition(trade, PaperState.EXPIRED, instant)
            trade.failure_reason = "MISSING_POST_LATENCY_BOOK"
            return True
        return False
    if trade.model != "optimistic":
        if (first.requested_at or first.received_at) < trade.due_at or (
            second.requested_at or second.received_at
        ) < trade.due_at:
            if instant > opportunity.expires_at:
                transition(trade, PaperState.EXPIRED, instant)
                trade.failure_reason = "MISSING_POST_LATENCY_BOOK"
                return True
            return False
        reasons = book_reasons(first, a, instant, trade.settings.max_quote_age_ms)
        reasons += book_reasons(second, b, instant, trade.settings.max_quote_age_ms)
        if abs((first.received_at - second.received_at).total_seconds() * 1000) > (
            trade.settings.max_book_skew_ms
        ):
            reasons.append("BOOK_TIME_SKEW")
        if not a.fee.known_at(instant) or not b.fee.known_at(instant):
            reasons.append("UNKNOWN_FEE")
        if reasons:
            transition(trade, PaperState.EXPIRED, instant)
            trade.failure_reason = ",".join(reasons)
            return True
    if instant > opportunity.expires_at and trade.model != "optimistic":
        transition(trade, PaperState.EXPIRED, instant)
        trade.failure_reason = "OPPORTUNITY_EXPIRED"
        return True
    if trade.model == "observed":
        transition(trade, PaperState.FAILED, instant)
        trade.failure_reason = "OBSERVED_MODEL_REQUIRES_TRADE_AND_QUEUE_EVIDENCE"
        return True
    c = opportunity.calculation
    trade.first = simulate_leg(first, a, trade, c.limit_one)
    trade.second = simulate_leg(second, b, trade, c.limit_two)
    trade.fill_at = instant
    trade.unhedged_quantity = abs(trade.first.quantity - trade.second.quantity)
    total_cost = trade.first.cost + trade.second.cost + trade.first.fee + trade.second.fee
    total_cost += sum(
        (
            trade.settings.additional_costs.get(market.venue, AdditionalCosts()).total(leg.quantity)
            for market, leg in ((a, trade.first), (b, trade.second))
            if leg.quantity
        ),
        D("0"),
    )
    slip = (trade.first.cost + trade.second.cost) * trade.settings.slippage_bps / 10000
    if trade.unhedged_quantity:
        transition(trade, PaperState.UNHEDGED, instant)
        trade.failure_reason = "PARTIAL_OR_ONE_SIDED_FILL"
    elif trade.first.quantity:
        transition(trade, PaperState.HEDGED, instant)
        trade.locked_profit = trade.first.quantity - total_cost - slip
    else:
        transition(trade, PaperState.FAILED, instant)
        trade.failure_reason = "LIMIT_NOT_EXECUTABLE"
    return True


def settle(trade: PaperTrade, opportunity: Opportunity, a: Market, b: Market, at: datetime) -> bool:
    if trade.state not in (PaperState.HEDGED, PaperState.UNHEDGED):
        return False
    if a.result is None or b.result is None or a.status != "settled" or b.status != "settled":
        return False
    one = a.result if opportunity.first_outcome == Side.YES else 1 - a.result
    two = b.result if opportunity.second_outcome == Side.YES else 1 - b.result
    payout = trade.first.quantity * one + trade.second.quantity * two
    costs = trade.first.cost + trade.second.cost + trade.first.fee + trade.second.fee
    costs += sum(
        (
            trade.settings.additional_costs.get(market.venue, AdditionalCosts()).total(leg.quantity)
            for market, leg in ((a, trade.first), (b, trade.second))
            if leg.quantity
        ),
        D("0"),
    )
    trade.settlement_profit = payout - costs
    trade.settlement_at = at
    transition(trade, PaperState.SETTLED, at)
    return True

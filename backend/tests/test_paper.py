from datetime import timedelta

import pytest

from app.domain import D, PaperState, PaperTrade, now
from app.execution import DisabledExecution, LiveExecutionDisabled, OrderIntent
from app.paper import evaluate_paper, transition
from app.replay import replay_trade


def intent(opportunity, risk):
    at = now()
    trade = PaperTrade(
        id="test-intent",
        opportunity_id=opportunity.id,
        event_id=opportunity.event_id,
        league=opportunity.league,
        quantity=opportunity.calculation.quantity,
        decision_at=at,
        due_at=at + timedelta(milliseconds=400),
        expected_profit=opportunity.calculation.net_profit,
        reserved_capital=D("1000"),
        model="conservative",
        settings=risk,
        source="demo",
    )
    transition(trade, PaperState.READY, at)
    transition(trade, PaperState.SUBMITTED, at)
    return trade


def test_conservative_waits_for_later_books(scenario, opportunity):
    a, b, books, _, risk = scenario
    trade = intent(opportunity, risk)
    assert not evaluate_paper(trade, opportunity, books, a, b, trade.due_at, False)
    for book in books.values():
        book.received_at = trade.due_at
        book.exchange_at = trade.due_at
    assert evaluate_paper(trade, opportunity, books, a, b, trade.due_at, False)
    assert trade.state == PaperState.HEDGED
    assert trade.first.quantity == trade.second.quantity
    assert trade.locked_profit is not None
    assert trade.settlement_profit is None
    assert not evaluate_paper(trade, opportunity, books, a, b, trade.due_at, False)


def test_one_sided_fill_is_not_profit(scenario, opportunity):
    a, b, books, _, risk = scenario
    trade = intent(opportunity, risk)
    for book in books.values():
        book.received_at = book.exchange_at = trade.due_at
    book = books[(b.id, opportunity.second_outcome)]
    book.asks = []
    assert evaluate_paper(trade, opportunity, books, a, b, trade.due_at, False)
    assert trade.state == PaperState.UNHEDGED
    assert trade.locked_profit is None
    assert trade.unhedged_quantity > 0


def test_kill_cancels_pending(scenario, opportunity):
    a, b, books, _, risk = scenario
    trade = intent(opportunity, risk)
    assert evaluate_paper(trade, opportunity, books, a, b, trade.due_at, True)
    assert trade.state == PaperState.CANCELLED


def test_changed_rules_cancel_intent(scenario, opportunity):
    a, b, books, _, risk = scenario
    trade = intent(opportunity, risk)
    a.rules.overtime = "excluded"
    assert evaluate_paper(trade, opportunity, books, a, b, trade.due_at, False)
    assert trade.state == PaperState.CANCELLED


def test_missing_post_latency_book_expires(scenario, opportunity):
    a, b, books, _, risk = scenario
    trade = intent(opportunity, risk)
    at = opportunity.expires_at + timedelta(milliseconds=1)
    assert evaluate_paper(trade, opportunity, books, a, b, at, False)
    assert trade.state == PaperState.EXPIRED


def test_replay_matches_post_latency_execution(scenario, opportunity):
    a, b, books, _, risk = scenario
    trade = intent(opportunity, risk)
    events = []
    for book in books.values():
        book.received_at = book.exchange_at = trade.due_at
        events.append(book)
    result, steps = replay_trade(trade, opportunity, events)
    assert steps
    assert result.state == PaperState.HEDGED
    assert trade.state == PaperState.SUBMITTED


def test_invalid_state_transition(scenario, opportunity):
    trade = intent(opportunity, scenario[-1])
    with pytest.raises(ValueError):
        transition(trade, PaperState.CREATED, now())


async def test_live_methods_fail_explicitly(opportunity):
    execution = DisabledExecution()
    order = OrderIntent(
        idempotency_key="test",
        market_id=opportunity.first_market_id,
        side=opportunity.first_outcome,
        quantity=D("1"),
        limit_price=D("0.5"),
    )
    with pytest.raises(LiveExecutionDisabled):
        await execution.place_limit_order(order)

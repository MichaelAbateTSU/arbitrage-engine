from datetime import timedelta

import pytest
from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from pydantic import ValidationError

from app.arbitrage import calculate, detect
from app.config import Settings
from app.domain import D, FeeSpec, Level, Side, now
from app.matching import Alias, extract_rules, match_markets, normalize_team, parse_semantic_result
from app.pricing import BookIntegrityError, LocalBook, consume, cost, fee, quantity


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity", 1.3])
def test_invalid_financial_values(value):
    with pytest.raises((ValueError, ValidationError)):
        Level(price=D("0.4"), quantity=value)


def test_us_fee_official_example():
    spec = FeeSpec(kind="us_quadratic", rate=D("0.0695"))
    assert fee([Level(price=D("0.65"), quantity=D("1000"))], spec) == D("15.81")
    assert fee([Level(price=D("0.5"), quantity=D("1000"))], spec) == D("17.38")


def test_international_current_fee():
    spec = FeeSpec(kind="international", rate=D("0.05"))
    assert fee([Level(price=D("0.5"), quantity=D("100"))], spec) == D("1.25000")


def test_kalshi_conservative_bound():
    spec = FeeSpec(kind="kalshi_bound", rate=D("0.07"))
    assert fee([Level(price=D("0.5"), quantity=D("100"))], spec) >= D("1.75")


def test_depth_and_per_venue_caps(scenario):
    a, b, books, _, risk = scenario
    calculation = calculate(books[(a.id, Side.YES)], books[(b.id, Side.NO)], a, b, risk)
    assert calculation
    assert calculation.quantity == D("975")
    assert calculation.net_profit == D("64.084500")
    assert (
        calculation.cost_one
        + calculation.fee_one
        + calculation.slippage / 2
        + calculation.safety_buffer / 2
        <= D("500")
    )
    assert (
        calculation.cost_two
        + calculation.fee_two
        + calculation.slippage / 2
        + calculation.safety_buffer / 2
        <= D("500")
    )
    assert calculation.quantity <= quantity(books[(a.id, Side.YES)].asks)
    assert calculation.net_profit < calculation.gross_profit


@pytest.mark.parametrize(
    "field, expected",
    [
        ("kill_switch", "KILL_SWITCH_ACTIVE"),
        ("blocked", "MARKET_BLOCKLISTED"),
        ("unknown_fee", "UNKNOWN_FEE"),
        ("stale", "BOOK_STALE"),
        ("unsynchronized", "BOOK_UNSYNCHRONIZED"),
        ("disconnected", "VENUE_UNHEALTHY"),
        ("tick", "INVALID_TICK_SIZE"),
        ("changed_rules", "MARKET_MATCH_UNAPPROVED"),
    ],
)
def test_fail_closed(scenario, field, expected):
    a, b, books, match, risk = scenario
    if field == "kill_switch":
        risk.kill_switch = True
    elif field == "blocked":
        risk.blocked_markets = [a.id]
    elif field == "unknown_fee":
        a.fee = FeeSpec()
    elif field == "changed_rules":
        a.rules.overtime = "excluded"
    elif field == "tick":
        a.price_ranges = []
    else:
        for book in books.values():
            if field == "stale":
                book.received_at = now() - timedelta(minutes=1)
                book.exchange_at = book.received_at
            elif field == "unsynchronized":
                book.synchronized = False
            elif field == "disconnected":
                book.connected = False
    opportunities, failures = detect(match, a, b, books, risk, now())
    assert not any(x.risk_status == "qualified" for x in opportunities)
    assert expected in failures + [reason for x in opportunities for reason in x.reasons]


def test_rule_mismatch_cannot_be_human_overridden(scenario):
    a, b, _, _, _ = scenario
    b.rules.overtime = "excluded"
    match = match_markets(a, b, human_reviewed=True)
    assert match.status == "rejected"
    assert "OVERTIME_MISMATCH" in match.reasons


def test_variable_cancellation_payout_rejected(scenario):
    a, b, _, _, _ = scenario
    a.rules.cancellation = b.rules.cancellation = "fair_price"
    assert "NONCONSTANT_CANCELLATION_PAYOUT" in match_markets(a, b).reasons


def test_unknown_rules_queue_review(scenario):
    a, b, _, _, _ = scenario
    b.rules.settlement_source = None
    assert match_markets(a, b).status == "review"


def test_different_raw_rules_require_review_even_if_extracted_fields_match(scenario):
    a, b, _, _, _ = scenario
    b.rules_text += " Stat corrections are accepted for 48 hours."
    assert match_markets(a, b).status == "review"
    assert "RULE_TEXT_REVIEW_REQUIRED" in match_markets(a, b).reasons
    assert match_markets(a, b, human_reviewed=True).status == "approved"


def test_draw_refund_is_not_invented_cancellation_rule():
    rules = extract_rules("If the game is a draw, this market will resolve 50-50.")
    assert rules.cancellation is None


def test_public_reschedule_invalidates_rule_version(scenario):
    a, b, _, match, _ = scenario
    old = b.rules_hash
    b.raw = {**b.raw, "gameStartTime": "2026-10-15T20:00:00Z"}
    assert b.rules_hash != old
    assert b.rules_hash != match.second_rules_hash


def test_future_exchange_clock_drift_fails_closed(scenario):
    a, b, books, match, risk = scenario
    for book in books.values():
        book.exchange_at = now() + timedelta(seconds=5)
    opportunities, failures = detect(match, a, b, books, risk, now())
    assert not opportunities
    assert "CLOCK_DRIFT" in failures


def test_slow_rest_response_is_not_fresh_merely_on_receipt(scenario):
    a, b, books, match, risk = scenario
    for book in books.values():
        book.requested_at = now() - timedelta(seconds=5)
        book.received_at = now()
    opportunities, failures = detect(match, a, b, books, risk, now())
    assert not opportunities
    assert "BOOK_STALE" in failures


def test_usdc_requires_explicit_paper_currency_assumption(scenario):
    a, b, books, _, risk = scenario
    b.quote_currency = "USDC"
    match = match_markets(a, b)
    opportunities, failures = detect(match, a, b, books, risk, now())
    assert not opportunities
    assert "CURRENCY_ASSUMPTION_UNACKNOWLEDGED" in failures
    risk.allow_usdc_parity_assumption = True
    opportunities, failures = detect(match, a, b, books, risk, now())
    assert any(x.risk_status == "qualified" for x in opportunities)


def test_curated_aliases_never_guess_ambiguous_cities():
    from app.teams import builtin_aliases

    aliases = builtin_aliases()
    assert normalize_team("BUF Bills", "NFL", aliases) == "buffalo bills"
    assert normalize_team("Los Angeles R", "NFL", aliases) == "los angeles rams"
    assert normalize_team("Los Angeles", "NFL", aliases) == "los angeles"
    assert normalize_team("BOS Red Sox", "MLB", aliases) == "boston red sox"


def test_inverted_outcome_pair(scenario):
    a, b, _, _, _ = scenario
    b.yes_team = b.participants[1]
    assert match_markets(a, b).inverted
    a.rules.draw = b.rules.draw = "no"
    assert match_markets(a, b).status == "rejected"


def test_alias_is_league_scoped():
    aliases = [
        Alias(league="NFL", original="BUF Bills", canonical="Buffalo Bills", source="official")
    ]
    assert normalize_team(" BUF. Bills ", "NFL", aliases) == "buffalo bills"
    assert normalize_team("BUF Bills", "NBA", aliases) == "buf bills"
    assert normalize_team("Atlántá", "NFL", []) == "atlanta"


def test_semantic_schema_never_approves():
    with pytest.raises(ValidationError):
        parse_semantic_result('{"candidate_match":true,"confidence":1}')


def test_duplicate_and_gap_delta(scenario):
    a, _, books, _, _ = scenario
    book = books[(a.id, Side.YES)]
    book.sequence = 10
    local = LocalBook(book)
    assert not local.delta("asks", D("0.4"), D("400"), now(), 10)
    assert local.book.asks[0].quantity == D("600")
    with pytest.raises(BookIntegrityError, match="SEQUENCE_GAP"):
        local.delta("asks", D("0.4"), D("400"), now(), 12)
    assert not local.book.synchronized
    assert not local.book.connected


def test_out_of_order_and_negative_delta(scenario):
    a, _, books, _, _ = scenario
    local = LocalBook(books[(a.id, Side.YES)])
    with pytest.raises(BookIntegrityError):
        local.delta("asks", D("0.4"), D("-999"), now(), additive=True)
    assert not local.book.synchronized


@pytest.mark.parametrize(
    "field",
    [
        "trading_mode",
        "live_trading_enabled",
        "kalshi_execution_enabled",
        "polymarket_execution_enabled",
    ],
)
def test_live_cannot_be_enabled(field):
    with pytest.raises(ValidationError, match="LIVE_EXECUTION_NOT_IMPLEMENTED"):
        Settings(**{field: "live" if field == "trading_mode" else True})


def test_production_never_has_default_password():
    with pytest.raises(ValidationError):
        Settings(environment="production")


@given(st.integers(1, 99), st.integers(1, 500), st.integers(1, 500))
@hypothesis_settings(max_examples=60)
def test_depth_properties(price, one, two):
    ladder = [
        Level(price=D(price) / 100, quantity=D(one)),
        Level(price=min(D("1"), D(price + 1) / 100), quantity=D(two)),
    ]
    first = consume(ladder, D(one))
    all_levels = consume(ladder, D(one + two))
    assert cost(first) / quantity(first) <= cost(all_levels) / quantity(all_levels)
    assert quantity(consume(ladder, D(one + two + 1))) <= D(one + two)
    spec = FeeSpec(kind="international", rate=D("0.05"))
    gross = D(one + two) - cost(all_levels)
    assert gross - fee(all_levels, spec) <= gross


def test_hedged_quantity_rounding_and_sizing_modes(scenario):
    a, b, books, match, risk = scenario
    for mode in (
        "per_venue",
        "total_notional",
        "fixed_quantity",
        "max_depth",
        "target_profit",
        "bankroll",
    ):
        risk.sizing_mode = mode
        ops, _ = detect(match, a, b, books, risk, now())
        for op in ops:
            assert op.calculation.quantity % a.quantity_step == 0
            assert op.calculation.quantity % b.quantity_step == 0

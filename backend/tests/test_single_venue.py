from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from pydantic import ValidationError

from app.config import Settings
from app.domain import Book, D, FeeSpec, Level, PriceRange, Side, now
from app.eligibility import decode_kalshi_scope
from app.pricing import complementary_book, cost, fee
from app.single_venue import (
    TERMS_URL,
    EpisodeTracker,
    Predicate,
    ResearchMarket,
    execution_stress,
    parse_market,
    payout_floor,
    quote_bundle,
    quote_pair,
    relationships,
)
from app.venues.http import PublicHTTP, VenueError
from app.venues.single_venue import BASE, SingleVenueReader, account_evidence


def market(ticker, strike, family="KXBTCD", predicate=None):
    return ResearchMarket(
        ticker=ticker,
        event_ticker="same-event",
        series_ticker=family,
        measurement_end=now() + timedelta(hours=1),
        predicate=predicate or Predicate(kind="greater", lower=str(strike)),
        rules_hash=ticker + "-hash",
        active=True,
        price_ranges=[PriceRange(start="0", end="1", step="0.01")],
        fee=FeeSpec(kind="kalshi_bound", rate="0.07", valid_until=now() + timedelta(minutes=10)),
    )


@pytest.fixture
def pair():
    first, second = market("lower", 100), market("higher", 200)
    second.measurement_end = first.measurement_end
    return first, second


def books(pair, first_price="0.45", second_price="0.45"):
    first, second = pair
    return {
        (first.ticker, Side.YES): Book(
            market_id=first.ticker,
            outcome=Side.YES,
            asks=[Level(price=first_price, quantity="100")],
            received_at=now(),
        ),
        (second.ticker, Side.NO): Book(
            market_id=second.ticker,
            outcome=Side.NO,
            asks=[Level(price=second_price, quantity="100")],
            received_at=now(),
        ),
    }


@pytest.fixture
def wire():
    return {
        "ticker": "KXBTCD-26OCT0812-T81999.99",
        "event_ticker": "KXBTCD-26OCT0812",
        "market_type": "binary",
        "notional_value_dollars": "1",
        "status": "active",
        "close_time": "2026-10-08T16:00:00Z",
        "strike_type": "greater",
        "floor_strike": "81999.99",
        "rules_primary": "If the simple average of the sixty seconds of CF Benchmarks' "
        "Bitcoin Real-Time Index (BRTI) before 12 PM EDT is above 81999.99 at "
        "12 PM EDT on Oct 8, 2026, then the market resolves to Yes.",
        "rules_secondary": "Shared settlement source description",
        "price_ranges": [{"start": "0", "end": "1", "step": "0.01"}],
    }


def parse(wire):
    sources = [
        {
            "name": "CF Benchmarks",
            "url": "https://www.cfbenchmarks.com/data/indices/BRTI",
        }
    ]
    return parse_market(
        wire,
        {
            "event_ticker": wire["event_ticker"],
            "series_ticker": "KXBTCD",
            "strike_date": "2026-10-08T16:00:00Z",
            "settlement_sources": sources,
        },
        {"ticker": "KXBTCD", "contract_terms_url": TERMS_URL, "settlement_sources": sources},
        FeeSpec(kind="kalshi_bound", rate="0.07"),
    )


def test_source_metadata_cannot_be_silently_overridden_by_rule_text(wire):
    with pytest.raises(VenueError, match="PRODUCT_UNSUPPORTED"):
        parse_market(
            wire,
            {
                "event_ticker": wire["event_ticker"],
                "series_ticker": "KXBTCD",
                "strike_date": "2026-10-08T16:00:00Z",
                "settlement_sources": [],
            },
            {"ticker": "KXBTCD", "contract_terms_url": TERMS_URL, "settlement_sources": []},
            FeeSpec(),
        )


def test_parse_uses_rules_not_ticker_or_occurrence_datetime(wire):
    wire["ticker"] = "unhelpful-ticker"
    wire["occurrence_datetime"] = "2026-10-08T16:05:00Z"
    result = parse(wire)
    assert result.measurement_end == datetime(2026, 10, 8, 16, tzinfo=UTC)
    assert result.predicate.lower == D("81999.99")
    assert result.predicate.pays_yes(D("81999.99")) is False
    assert result.predicate.pays_yes(D("81999.995")) is True


@pytest.mark.parametrize(
    "change",
    [
        {"floor_strike": "82000"},
        {"market_type": "scalar"},
        {"close_time": "2026-10-08T16:05:00Z"},
        {"strike_type": "greater_or_equal"},
        {"notional_value_dollars": "100"},
    ],
)
def test_conflicting_identity_is_rejected(wire, change):
    wire.update(change)
    with pytest.raises((VenueError, ValueError)):
        parse(wire)


def test_rule_changes_invalidate_hash_but_price_observations_do_not(wire):
    baseline = parse(wire).rules_hash
    wire["yes_ask_dollars"] = "0.60"
    assert parse(wire).rules_hash == baseline
    wire["rules_secondary"] = "Different source policy"
    assert parse(wire).rules_hash != baseline


@pytest.mark.parametrize("value", [True, 1.1, "NaN", "Infinity", "not-decimal"])
def test_predicate_exact_boundaries(value):
    with pytest.raises(ValidationError):
        Predicate(kind="greater", lower=value)


def test_monotone_floor_and_reverse_direction_counterexample():
    lower, higher = Predicate(kind="greater", lower="100"), Predicate(kind="greater", lower="200")
    assert payout_floor([lower, higher], [Side.YES, Side.NO]) == (D("1"), D("2"), D("1"))
    assert payout_floor([lower, higher], [Side.NO, Side.YES])[0] == 0


def test_disjoint_ranges_no_pair_needs_no_exhaustiveness():
    a = Predicate(kind="between", lower="100", upper="199.99")
    b = Predicate(kind="between", lower="200", upper="299.99")
    assert payout_floor([a, b], [Side.NO, Side.NO]) == (D("1"), D("2"), D("2"))
    assert a.pays_yes(D("199.995")) is False
    assert b.pays_yes(D("199.995")) is False


def test_shared_boundary_breaks_exclusivity():
    a = Predicate(kind="between", lower="100", upper="200")
    b = Predicate(kind="between", lower="200", upper="300")
    assert payout_floor([a, b], [Side.NO, Side.NO])[0] == 0


def test_multi_no_basket_can_be_positive_when_all_two_leg_pairs_are_negative():
    markets = [
        market(
            f"range-{index}",
            0,
            "KXBTC",
            Predicate(kind="between", lower=str(100 * index), upper=str(100 * index + 99)),
        )
        for index in range(3)
    ]
    for value in markets:
        value.measurement_end = markets[0].measurement_end
    observed = {
        (value.ticker, Side.NO): Book(
            market_id=value.ticker,
            outcome=Side.NO,
            received_at=now(),
            asks=[Level(price="0.60", quantity="100")],
        )
        for value in markets
    }
    for first, side_one, second, side_two in relationships(markets):
        pair = quote_pair(first, side_one, second, side_two, observed, now())
        assert D(pair["best_conditional_net_before_additional_costs"]) < 0
    bundle = quote_bundle(markets, [Side.NO] * 3, observed, now())
    assert bundle["normal_floor"] == "2"
    assert bundle["no_data_floor"] == "3"
    assert D(bundle["best_conditional_net_before_additional_costs"]) > 0
    assert bundle["eligible"] is False


def test_exhaustive_looking_yes_basket_loses_on_no_data_and_decimal_gaps():
    predicates = [
        Predicate(kind="less", lower="100"),
        Predicate(kind="between", lower="100", upper="199.99"),
        Predicate(kind="greater", lower="199.99"),
    ]
    assert payout_floor(predicates, [Side.YES] * 3)[0] == 1
    assert payout_floor(predicates, [Side.YES] * 3)[2] == 0
    predicates[-1] = Predicate(kind="greater", lower="200")
    assert payout_floor(predicates, [Side.YES] * 3)[0] == 0


def test_relationships_do_not_cross_measurement_groups(pair):
    a, b = pair
    assert len(relationships([a, b])) == 1
    b.event_ticker = "different"
    assert relationships([a, b]) == []


def test_depth_profit_is_conditional_not_eligible_even_when_positive(pair):
    result = quote_pair(pair[0], Side.YES, pair[1], Side.NO, books(pair), now())
    assert D(result["best_conditional_net_before_additional_costs"]) > 0
    assert result["best_conditional_size"] == 100
    assert result["minimum_all_scenario_payout"] is None
    assert result["eligible"] is False and result["shadow_qualified"] is False
    assert "OUTCOME_REVIEW_JOINT_PAYOUT_FLOOR_UNVERIFIED" in result["reasons"]


def test_pruning_nonpositive_gross_matches_brute_force(pair):
    observed = books(pair, "0.51", "0.50")
    result = quote_pair(pair[0], Side.YES, pair[1], Side.NO, observed, now())
    first, second = pair
    nets = []
    for q in range(1, 101):
        one = [Level(price="0.51", quantity=str(q))]
        two = [Level(price="0.50", quantity=str(q))]
        acquisition = cost(one) + cost(two)
        nets.append(
            D(q)
            - acquisition
            - fee(one, first.fee)
            - fee(two, second.fee)
            - acquisition * D("0.0025")
        )
    assert D(result["best_conditional_net_before_additional_costs"]) == max(nets)
    assert result["best_conditional_size"] == 1


def test_sizing_walks_deeper_and_chooses_maximum_profit(pair):
    observed = books(pair)
    for book in observed.values():
        book.asks = [Level(price="0.45", quantity="10"), Level(price="0.70", quantity="90")]
    result = quote_pair(pair[0], Side.YES, pair[1], Side.NO, observed, now())
    assert result["best_conditional_size"] == 10


def test_both_legs_share_one_venue_capital_limit(pair):
    result = quote_pair(pair[0], Side.YES, pair[1], Side.NO, books(pair), now(), max_capital=D("1"))
    assert result["best_conditional_size"] == 1
    assert D(result["required_full_purchase_capital"]) <= 1


@pytest.mark.parametrize("case", ["missing", "stale", "identity", "grid", "fee", "future", "skew"])
def test_bad_pricing_evidence_never_becomes_positive(pair, case):
    observed = books(pair)
    first = observed[(pair[0].ticker, Side.YES)]
    second = observed[(pair[1].ticker, Side.NO)]
    if case == "missing":
        observed.pop((pair[1].ticker, Side.NO))
    elif case == "stale":
        first.requested_at = now() - timedelta(seconds=10)
    elif case == "identity":
        first.market_id = "wrong"
    elif case == "grid":
        first.asks[0].price = D("0.451")
    elif case == "fee":
        pair[0].fee = FeeSpec()
    elif case == "future":
        first.received_at = now() + timedelta(seconds=10)
    elif case == "skew":
        second.received_at = first.received_at - timedelta(seconds=2)
    result = quote_pair(pair[0], Side.YES, pair[1], Side.NO, observed, now())
    assert result["best_conditional_size"] is None
    assert not result["economic_positive_before_additional_costs"]


def test_no_price_grid_is_complemented_from_yes_grid(pair):
    pair[1].price_ranges = [PriceRange(start="0.1", end="0.2", step="0.01")]
    observed = books(pair, "0.10", "0.80")
    result = quote_pair(pair[0], Side.YES, pair[1], Side.NO, observed, now())
    assert result["best_conditional_size"] is not None


def test_repeated_quotes_are_one_conditional_episode_with_coverage_gap(pair):
    tracker = EpisodeTracker()
    result = quote_pair(pair[0], Side.YES, pair[1], Side.NO, books(pair), now())
    tracker.observe([result], now())
    tracker.observe([result], now())
    missing = {**result, "best_conditional_size": None}
    tracker.observe([missing], now())
    tracker.observe([result], now())
    assert len(tracker.active) == 1
    assert next(iter(tracker.active.values()))["observations"] == 3
    assert next(iter(tracker.active.values()))["coverage_interrupted"] is True
    tracker.observe([{**result, "economic_positive_before_additional_costs": False}], now())
    tracker.observe([result], now())
    assert len(tracker.closed) == 1 and len(tracker.active) == 1


def test_changed_specs_close_old_episode_and_failed_cohort_marks_gap(pair):
    tracker = EpisodeTracker()
    result = quote_pair(pair[0], Side.YES, pair[1], Side.NO, books(pair), now())
    tracker.observe([result], now())
    tracker.interrupt(result["event_ticker"])
    assert tracker.active[result["id"]]["coverage_interrupted"] is True
    changed = {**result, "id": "new-rule-bound-identity"}
    tracker.observe([changed], now())
    assert len(tracker.closed) == 1
    assert tracker.closed[0]["end_reason"] == "SPECIFICATION_CHANGED"
    assert list(tracker.active) == ["new-rule-bound-identity"]


def test_reordered_legs_are_not_new_opportunity_episodes(pair):
    original = quote_pair(pair[0], Side.YES, pair[1], Side.NO, books(pair), now())
    reordered = quote_pair(pair[1], Side.NO, pair[0], Side.YES, books(pair), now())
    assert original["id"] == reordered["id"]
    tracker = EpisodeTracker()
    tracker.observe([original, reordered], now())
    assert len(tracker.active) == 1
    assert tracker.active[original["id"]]["observations"] == 2


async def test_public_reader_never_loads_env_settings(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Public reader must not load credentials")

    monkeypatch.setattr("app.venues.single_venue.Settings", forbidden)
    async with httpx.AsyncClient() as http:
        reader = SingleVenueReader(PublicHTTP(http))
        assert reader.fees.base == BASE
        assert reader.fees.btc_enabled is False


def test_snapshot_bids_are_converted_not_used_as_purchase_prices():
    yes, no = complementary_book(
        "ticker",
        [Level(price="0.40", quantity="10")],
        [Level(price="0.55", quantity="20")],
        received_at=now(),
    )
    assert yes.asks[0].price == D("0.45")
    assert no.asks[0].price == D("0.60")


@pytest.mark.parametrize(
    "invalid",
    [
        [["0.5", "-1"]],
        [["0.5", True]],
        [[1.1, "1"]],
        [["NaN", "1"]],
        [["0.5", "1", "extra"]],
    ],
)
def test_invalid_snapshot_rejected(invalid):
    with pytest.raises(ValueError):
        SingleVenueReader._levels(invalid)


def test_zero_snapshot_quantity_is_removed_without_fabricating_depth():
    assert SingleVenueReader._levels([["0.5", "0"]]) == []


def test_read_scope_does_not_establish_account_kyc_or_expose_key():
    evidence = decode_kalshi_scope(
        {
            "api_keys": [
                {"api_key_id": "test-sensitive", "name": "private-name", "scopes": ["write::trade"]}
            ],
            "api_key_region_expiration_ts": 100,
        },
        "test-sensitive",
    )
    assert evidence["trading_scope"] == "verified"
    assert "test-sensitive" not in str(evidence) and "private-name" not in str(evidence)
    assert "account_order_permission" not in evidence


def test_synthetic_failure_losses_and_insufficient_unwind_depth(pair):
    observed = books(pair, "0.45", "0.45")
    first = observed[(pair[0].ticker, Side.YES)]
    second = observed[(pair[1].ticker, Side.NO)]
    unwind = Book(
        market_id=pair[0].ticker,
        outcome=Side.YES,
        bids=[Level(price="0.40", quantity="100")],
        received_at=now(),
    )
    rejected = execution_stress(first, second, unwind, D("100"), pair[0].fee, "rejected_second")
    assert D(rejected["realized_unwind_cashflow"]) < 0
    partial = execution_stress(first, second, unwind, D("100"), pair[0].fee, "partial_second")
    assert partial["matched_quantity"] == "50"
    delayed = second.model_copy(update={"asks": []})
    assert (
        execution_stress(first, delayed, unwind, D("100"), pair[0].fee, "delayed")[
            "matched_quantity"
        ]
        == "0"
    )
    unwind.bids = []
    unresolved = execution_stress(first, second, unwind, D("100"), pair[0].fee, "rejected_second")
    assert unresolved["conditional_pnl_at_unit_floor"] is None
    assert unresolved["unresolved_quantity"] == "100"
    assert unresolved["source"] == "synthetic_stress" and unresolved["qualified"] is False


async def test_account_reader_does_not_mutate_or_treat_scope_as_permission(monkeypatch):
    monkeypatch.setattr("app.venues.single_venue.kalshi_headers", lambda *_: {})
    settings = Settings(_env_file=None, kalshi_api_key="test-key")
    with respx.mock as routes:
        routes.get(f"{BASE}/api_keys").respond(
            200, json={"api_keys": [{"api_key_id": "test-key", "scopes": ["write::trade"]}]}
        )
        routes.get(f"{BASE}/portfolio/subaccounts/netting").respond(
            200,
            json={
                "netting_configs": [{"subaccount_number": 0, "exchange_index": 2, "enabled": False}]
            },
        )
        async with httpx.AsyncClient() as http:
            result = await account_evidence(http, settings)
        assert all(call.request.method == "GET" for call in routes.calls)
    assert result["primary_crypto_netting"] is False
    assert result["account_order_permission"] == result["kyc"] == "unverified"
    assert result["settings_changed"] is False


async def test_changed_terms_stop_probe_before_any_market_or_order_request():
    with respx.mock as routes:
        routes.get(TERMS_URL).respond(200, content=b"changed terms")
        async with httpx.AsyncClient() as http:
            with pytest.raises(VenueError, match="TERMS_CHANGED"):
                await SingleVenueReader(PublicHTTP(http)).verify_terms()
        assert len(routes.calls) == 1


@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("series", "INVALID_SERIES_METADATA"),
        ("event", "INVALID_EVENT_METADATA"),
        ("market", "COHORT_NO_SUPPORTED_OPEN_CONTRACTS"),
        ("fee", "INVALID_EVENT_FEE_METADATA"),
        ("books", "INVALID_BATCH_BOOK_RESPONSE"),
        ("identity", "BATCH_BOOK_IDENTITY_MISMATCH"),
        ("valid", None),
    ],
)
async def test_cohort_provider_boundaries_fail_explicitly(wire, case, code):
    from datetime import timezone

    end = (now() + timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
    eastern = end.astimezone(timezone(timedelta(hours=-4)))
    clock = eastern.strftime("%I %p").lstrip("0")
    wire["close_time"] = end.isoformat()
    wire["rules_primary"] = (
        "If the simple average of the sixty seconds of CF Benchmarks' "
        f"Bitcoin Real-Time Index (BRTI) before {clock} EDT is above 81999.99 at "
        f"{clock} EDT on {eastern.strftime('%b')} {eastern.day}, {eastern.year}, "
        "then the market resolves to Yes."
    )
    sources = [{"name": "CF Benchmarks", "url": "https://www.cfbenchmarks.com/data/indices/BRTI"}]
    series = {
        "ticker": "KXBTCD",
        "contract_terms_url": TERMS_URL,
        "settlement_sources": sources,
        "fee_type": "quadratic",
        "fee_multiplier": "not-decimal" if case == "fee" else "1",
    }
    event = {
        "event_ticker": wire["event_ticker"],
        "series_ticker": series["ticker"],
        "strike_date": end.isoformat(),
        "settlement_sources": sources,
        "markets": [None] if case == "market" else [wire],
    }
    book_rows = (
        [None]
        if case == "books"
        else [
            {
                "ticker": "incorrect" if case == "identity" else wire["ticker"],
                "orderbook_fp": {"yes_dollars": [["0.40", "10"]], "no_dollars": [["0.55", "20"]]},
            }
        ]
    )
    with respx.mock as routes:
        routes.get(f"{BASE}/series/KXBTCD").respond(
            200, json={"series": None if case == "series" else series}
        )
        routes.get(f"{BASE}/events/{wire['event_ticker']}").respond(
            200, json={"event": None if case == "event" else event}
        )
        routes.get(f"{BASE}/events/fee_changes").respond(
            200, json={"event_fee_changes": [], "cursor": ""}
        )
        routes.get(f"{BASE}/markets/orderbooks").respond(200, json={"orderbooks": book_rows})
        async with httpx.AsyncClient() as http:
            reader = SingleVenueReader(PublicHTTP(http))
            specification = {"event_ticker": wire["event_ticker"], "series": series}
            if code:
                with pytest.raises(VenueError, match=code):
                    await reader.cohort(specification)
            else:
                _, parsed, observed, errors = await reader.cohort(specification)
                assert len(parsed) == 1 and errors == []
                assert observed[(wire["ticker"], Side.YES)].asks[0].price == D("0.45")
                assert all(
                    book.exchange_at is None
                    and book.requested_at is not None
                    and book.requested_at <= book.received_at
                    for book in observed.values()
                )
        assert all(call.request.method == "GET" for call in routes.calls)

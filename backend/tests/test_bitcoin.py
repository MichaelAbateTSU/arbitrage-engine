import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest
import respx
from conftest import TEST_PASSWORD
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.arbitrage import detect
from app.bitcoin import brti_average
from app.config import Settings
from app.db import RiskRow
from app.domain import (
    BitcoinPolicy,
    BitcoinWindow,
    D,
    Exposure,
    Level,
    PaperState,
    PaperTrade,
    RiskSettings,
    Side,
    Venue,
)
from app.eligibility import PRODUCTS, Eligibility
from app.main import create_app
from app.matching import event_identity, match_markets
from app.paper import evaluate_paper
from app.pricing import BookIntegrityError, complementary_book
from app.service import analysis_tick, rematch_all
from app.settlement import settlement_proof
from app.shadow import complete_trial, unwind_trial
from app.validation import diagnose
from app.venues.clients import KalshiClient, USClient
from app.venues.http import PublicHTTP, VenueError
from app.venues.mapper import kalshi_bitcoin_window, kalshi_market, us_market
from app.venues.schemas import KalshiMarket, USMarket


def documented_policy(**changes):
    values = {
        "sample_start_offset": -59,
        "sample_end_offset": 0,
        "rounding_mode": "half_even",
        "revision_deadline_seconds": 0,
        "missing_data": "half_refund",
        "discretionary_settlement": "excluded",
        **changes,
    }
    return BitcoinPolicy(
        **values,
        evidence={
            name: "Explicit SYNTHETIC test policy, not provider approval." for name in values
        },
    )


def wire_pair(start):
    end = start + timedelta(minutes=15)
    eastern = timezone(timedelta(hours=-4))

    def label(value):
        return value.astimezone(eastern).strftime("%I:%M %p EDT on %B %d, %Y")

    kalshi = {
        "ticker": "KXBTC15M-SYNTHETIC-00",
        "event_ticker": "KXBTC15M-SYNTHETIC",
        "title": "SYNTHETIC BTC 15m",
        "status": "active",
        "market_type": "binary",
        "open_time": (start - timedelta(seconds=90)).isoformat(),
        "close_time": end.isoformat(),
        "floor_strike": "85000.00",
        "strike_type": "greater_or_equal",
        "notional_value_dollars": "1.0000",
        "rules_primary": (
            f"Synthetic fixture: If the simple average of the sixty seconds of BRTI "
            f"before {label(end)} is at least the simple average of the sixty seconds "
            f"of BRTI before {label(start)}, then Yes."
        ),
        "rules_secondary": "Synthetic sixty-sample, two-decimal fixture.",
        "price_ranges": [{"start": "0", "end": "1", "step": "0.01"}],
    }
    us = {
        "id": "btc-synthetic",
        "slug": "cpc-btc-synthetic",
        "question": "SYNTHETIC BTC 15m",
        "description": (
            "SYNTHETIC BRTI: Up when closing simple average is greater than or equal to opening."
        ),
        "active": True,
        "closed": False,
        "status": "MARKET_STATUS_OPEN",
        "endDate": (end + timedelta(minutes=30)).isoformat(),
        "gameStartTime": start.isoformat(),
        "marketSides": [
            {"long": True, "identifier": "cpc-btc-synthetic", "description": "Yes"},
            {"long": False, "identifier": "cpc-btc-synthetic", "description": "No"},
        ],
        "feeCoefficient": "0.0695",
        "minimumTradeQty": "0.01",
        "orderPriceMinTickSize": "0.01",
        "assetPriceTerms": {
            "marketType": "ASSET_PRICE_MARKET_TYPE_UP_DOWN",
            "asset": {"assetClass": "ASSET_CLASS_CRYPTO", "symbol": "btc"},
            "indexSymbol": "BRTI",
            "horizon": "15m",
            "windowStart": start.isoformat(),
            "windowEnd": end.isoformat(),
            "priceToBeat": {"value": "85000.00", "currency": "USD"},
        },
    }
    return kalshi, us


@pytest.fixture
def btc(monkeypatch):
    start = datetime(2030, 1, 1, 14, 0, tzinfo=UTC)
    at = start + timedelta(minutes=2)
    monkeypatch.setattr("app.venues.mapper.now", lambda: at)
    raw_a, raw_b = wire_pair(start)
    a = kalshi_market(
        KalshiMarket.model_validate(raw_a),
        "BTC",
        {
            "ticker": "KXBTC15M",
            "contract_terms_url": "https://assets.kalshi.com/contract_terms/CRYPTO.pdf",
        },
        D("0.07"),
        [],
    )
    b = us_market(USMarket.model_validate(raw_b), [])
    for m in (a, b):
        m.fee.observed_at = start
        m.fee.valid_until = start + timedelta(minutes=15)
    books = {}
    for m, yes, no in ((a, "0.44", "0.55"), (b, "0.55", "0.44")):
        observed = complementary_book(
            m.id,
            [Level(price=D(yes), quantity=D("100"))],
            [Level(price=D(no), quantity=D("100"))],
            received_at=at,
            exchange_at=at,
            requested_at=at,
            source="public",
        )
        books.update({(book.market_id, book.outcome): book for book in observed})
    risk = RiskSettings(supported_leagues=["BTC"], max_contracts=20, kill_switch=False)
    return a, b, books, at, risk


def synthetic(btc):
    a, b, books, at, risk = btc
    for m in (a, b):
        m.source = "demo"
        m.bitcoin.policy = documented_policy()
    for book in books.values():
        book.source = "demo"
    return a, b, books, at, risk


def profiles():
    return {
        venue: Eligibility(
            venue=venue, product=PRODUCTS[venue], operator_country="US", operator_region="GA"
        )
        for venue in Venue
    }


def test_metadata_mapping_uses_rule_window_not_trading_open_or_expiration(btc):
    a, b, _, at, _ = btc
    assert a.bitcoin.window_start == b.bitcoin.window_start == at - timedelta(minutes=2)
    assert a.start_time != datetime.fromisoformat(a.raw["open_time"])
    assert b.bitcoin.window_end != datetime.fromisoformat(b.raw["endDate"])
    assert a.bitcoin.opening_reference == b.bitcoin.opening_reference == D("85000")
    assert a.quantity_step == b.quantity_step == D("0.01")
    assert a.yes_team == b.yes_team == "up"


def test_est_rule_times_and_midnight_are_exact():
    start, end = kalshi_bitcoin_window(
        "before 12:00 AM EST on January 2, 2030 is at least before 11:45 PM EST on January 1, 2030"
    )
    assert start == datetime(2030, 1, 2, 4, 45, tzinfo=UTC)
    assert end == datetime(2030, 1, 2, 5, 0, tzinfo=UTC)


def test_public_normal_agreement_is_not_auto_approved(btc):
    a, b, books, at, risk = btc
    match = match_markets(a, b, human_reviewed=True)
    assert match.status == "review"
    assert "BTC_SAMPLE_START_OFFSET_UNVERIFIED" in match.reasons
    assert "BTC_MISSING_DATA_PAYOUT_UNPROVEN" in match.reasons
    assert "BTC_DISCRETIONARY_PAYOUT_UNPROVEN" in match.reasons
    proof = settlement_proof(a, b)
    assert not proof["proven"]
    assert proof["direction_bounds"]["NO"]["minimum_combined_payout"] is None
    values = diagnose(match, a, b, books, risk, profiles(), at, Exposure())
    assert len(values) == 2
    assert values[0].calculation is not None
    assert values[0].calculation.net_profit > 0  # Conditional synthetic price signal, not a hedge.
    assert not any(v.shadow_qualified or v.executable_for_operator for v in values)
    assert "ADDITIONAL_COSTS_UNVERIFIED" in values[0].reasons
    assert "ORDER_PERMISSION_UNVERIFIED" in values[0].execution_reasons


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("opening_reference", D("85000.01"), "BTC_OPENING_REFERENCE_MISMATCH"),
        ("window_start", datetime(2030, 1, 1, 14, 15, tzinfo=UTC), "BTC_WINDOW_START_MISMATCH"),
    ],
)
def test_exact_identity_conflicts_reject_even_human_review(btc, field, value, reason):
    a, b, *_ = btc
    changes = {field: value}
    if field == "window_start":
        changes["window_end"] = value + timedelta(minutes=15)
    b.bitcoin = BitcoinWindow.model_validate({**b.bitcoin.model_dump(), **changes})
    assert reason in match_markets(a, b, True).reasons
    assert match_markets(a, b, True).status == "rejected"


def test_reference_and_policy_changes_invalidate_approvals_not_charts(btc):
    a, b, *_ = btc
    old = b.rules_hash
    b.raw["assetPriceTerms"]["chart"] = {"display": "different"}
    b.raw["assetPriceTerms"]["settlementPrice"] = {"value": "85000", "currency": "USD"}
    assert b.rules_hash == old
    b.bitcoin.opening_reference = D("85001")
    assert b.rules_hash != old
    assert event_identity(a) == event_identity(b)  # Same window, changed reference requires review.
    old = a.rules_hash
    a.bitcoin.policy = documented_policy()
    assert a.rules_hash != old


@pytest.mark.parametrize("duration", [60, 899, 901, 3600])
def test_non_fifteen_minute_windows_fail(duration):
    start = datetime(2030, 1, 1, 14, tzinfo=UTC)
    with pytest.raises(ValidationError):
        BitcoinWindow(window_start=start, window_end=start + timedelta(seconds=duration))


def test_timezone_naive_windows_and_undocumented_policy_fail():
    with pytest.raises(ValidationError):
        BitcoinWindow(
            window_start=datetime(2030, 1, 1, 14), window_end=datetime(2030, 1, 1, 14, 15)
        )
    with pytest.raises(ValidationError):
        BitcoinPolicy(rounding_mode="half_even")


@pytest.mark.parametrize("offset", [0, 1])
def test_expired_metadata_and_post_latency_fills_never_execute(btc, offset):
    a, b, books, _, risk = synthetic(btc)
    at = a.bitcoin.window_end + timedelta(seconds=offset)
    for book in books.values():
        book.received_at = book.requested_at = book.exchange_at = at
    ops, reasons = detect(match_markets(a, b), a, b, books, risk, at)
    assert not ops
    assert "BTC_WINDOW_EXPIRED_OR_TOO_SHORT" in reasons
    values = diagnose(match_markets(a, b), a, b, books, risk, profiles(), at, Exposure())
    assert not any(value.shadow_qualified for value in values)
    assert all(value.calculation is None for value in values)


def test_last_milliseconds_do_not_schedule_fill_past_window_end(btc):
    a, b, books, _, risk = synthetic(btc)
    at = a.bitcoin.window_end - timedelta(milliseconds=risk.latency_ms)
    assert (
        "BTC_WINDOW_EXPIRED_OR_TOO_SHORT" in detect(match_markets(a, b), a, b, books, risk, at)[1]
    )


def test_synthetic_complete_policy_enables_both_direction_price_checks(btc):
    a, b, books, at, risk = synthetic(btc)
    match = match_markets(a, b)
    assert match.status == "approved" and settlement_proof(a, b)["proven"]
    ops, reasons = detect(match, a, b, books, risk, at)
    assert not reasons and len(ops) == 2
    assert {(o.first_outcome, o.second_outcome) for o in ops} == {
        (Side.YES, Side.NO),
        (Side.NO, Side.YES),
    }
    first = next(o for o in ops if o.first_outcome == Side.YES)
    assert first.risk_status == "qualified"
    assert first.calculation.price_one == first.calculation.price_two == D("0.45")
    assert first.calculation.fee_one > 0 and first.calculation.fee_two > 0
    assert first.calculation.quantity == 20
    assert first.expires_at <= a.bitcoin.window_end


def test_sample_endpoint_difference_can_break_hedge_without_being_an_observed_loss():
    end = datetime(2030, 1, 1, 14, 15, tzinfo=UTC)
    common = [(end - timedelta(seconds=i), D("100")) for i in range(1, 60)]
    before = common + [(end - timedelta(seconds=60), D("100.60"))]
    inclusive = common + [(end, D("99.40"))]
    one = brti_average(
        before, end, documented_policy(sample_start_offset=-60, sample_end_offset=-1)
    )
    two = brti_average(inclusive, end, documented_policy())
    assert one == D("100.01") and two == D("99.99")
    assert D(one < D("100")) + D(two >= D("100")) == 0  # Conditional NO + Up counterexample.


@pytest.mark.parametrize("rounding,expected", [("half_even", "100.00"), ("half_up", "100.01")])
def test_index_rounding_ties_are_not_silently_assumed(rounding, expected):
    end = datetime(2030, 1, 1, 14, 15, tzinfo=UTC)
    values = [(end - timedelta(seconds=i), D("100.005")) for i in range(60)]
    assert brti_average(values, end, documented_policy(rounding_mode=rounding)) == D(expected)
    with pytest.raises(ValueError, match="INCOMPLETE"):
        brti_average(values[:-1], end, documented_policy())
    with pytest.raises(ValueError, match="INCOMPLETE"):
        brti_average(values[:-1] + [values[0]], end, documented_policy())


async def test_crypto_discovery_uses_category_and_continues_short_pages(btc):
    a, _, _, _, _ = btc
    _, raw = wire_pair(a.bitcoin.window_start)
    with respx.mock:
        route = respx.get("https://gateway.polymarket.us/v1/markets")
        route.side_effect = [
            httpx.Response(200, json={"markets": [raw]}),
            httpx.Response(200, json={"markets": []}),
        ]
        async with httpx.AsyncClient() as http:
            values = [m async for m in USClient(PublicHTTP(http)).discover_bitcoin()]
        assert len(values) == 1
        assert [call.request.url.params["offset"] for call in route.calls] == ["0", "1"]
        assert route.calls[0].request.url.params["categories"] == "crypto"


async def test_changed_reference_cannot_reuse_old_us_book(btc):
    _, b, *_ = btc
    _, raw = wire_pair(b.bitcoin.window_start)
    raw["assetPriceTerms"]["priceToBeat"]["value"] = "85000.01"
    with respx.mock:
        respx.get(f"https://gateway.polymarket.us/v1/market/slug/{b.external_id}").respond(
            200, json={"market": raw}
        )
        async with httpx.AsyncClient() as http:
            with pytest.raises(VenueError, match="MARKET_SPECIFICATION_CHANGED"):
                await USClient(PublicHTTP(http)).get_orderbooks(b)


async def test_expired_kalshi_book_never_makes_network_request(btc, monkeypatch):
    a, *_ = btc
    monkeypatch.setattr("app.venues.clients.now", lambda: a.bitcoin.window_end)
    with respx.mock:
        async with httpx.AsyncClient() as http:
            client = KalshiClient(PublicHTTP(http), Settings(_env_file=None))
            with pytest.raises(VenueError, match="BTC_WINDOW_EXPIRED"):
                await client.get_orderbooks(a)
        assert not respx.calls


async def test_persisted_btc_pipeline_and_expired_match_retirement(store, btc, monkeypatch):
    a, b, books, at, risk = synthetic(btc)
    monkeypatch.setattr("app.service.now", lambda: at)
    for market in (a, b):
        await store.save_market(market)
    await store.save_books(list(books.values()))
    async with store.sessions.begin() as session:
        row = await session.get(RiskRow, store.source)
        row.payload = risk.model_dump(mode="json")
    await rematch_all(store)
    await analysis_tick(store)
    trades = await store.paper_trades()
    assert len(trades) == 1 and trades[0].league == "BTC"
    assert trades[0].state == PaperState.SUBMITTED
    due = trades[0].due_at + timedelta(milliseconds=1)
    for book in books.values():
        book.received_at = book.requested_at = book.exchange_at = due
    await store.save_books(list(books.values()))
    monkeypatch.setattr("app.service.now", lambda: due)
    await analysis_tick(store)
    assert (await store.paper_trades())[0].state == PaperState.HEDGED
    monkeypatch.setattr("app.service.now", lambda: a.bitcoin.window_end)
    await rematch_all(store)
    assert not [m for m in await store.matches() if m.first_market_id == a.id]


async def test_api_cannot_override_published_independent_review(store, btc):
    a, *_ = btc
    a.source = store.source
    await store.save_market(a)
    app = create_app(store.settings)
    app.state.store = store
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        login = await client.post("/api/v1/auth/login", json={"password": TEST_PASSWORD})
        response = await client.put(
            f"/api/v1/markets/{a.id}/normalization",
            headers={"X-CSRF-Token": login.json()["csrf"]},
            json={
                "expected_rules_hash": a.rules_hash,
                "participants": a.participants,
                "yes_team": a.yes_team,
                "start_time": a.start_time.isoformat(),
                "rules": a.rules.model_dump(mode="json"),
                "bitcoin_policy": documented_policy().model_dump(mode="json"),
                "evidence": {
                    name: "Synthetic evidence"
                    for name in ("participants", "yes_team", "start_time")
                },
                "note": "Cannot waive known published contingencies.",
            },
        )
    assert response.status_code == 422
    assert response.json()["detail"] == "BTC_POLICY_CONTRADICTS_PUBLISHED_TERMS"


def test_feature_flag_does_not_enable_live_execution():
    assert not Settings(_env_file=None).btc_15m_enabled
    assert Settings(_env_file=None, btc_15m_enabled=True).trading_mode == "paper"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, btc_15m_enabled=True, live_trading_enabled=True)


def test_delayed_paper_fill_after_window_close_is_expired(btc):
    a, b, books, at, risk = synthetic(btc)
    op = detect(match_markets(a, b), a, b, books, risk, at)[0][0]
    trade = PaperTrade(
        id="synthetic-btc-trade",
        opportunity_id=op.id,
        event_id=op.event_id,
        league="BTC",
        quantity=op.calculation.quantity,
        decision_at=at,
        due_at=at + timedelta(milliseconds=risk.latency_ms),
        expected_profit=op.calculation.net_profit,
        reserved_capital=D("25"),
        settings=risk,
        model="conservative",
        state=PaperState.SUBMITTED,
        source="demo",
    )
    assert evaluate_paper(trade, op, books, a, b, a.bitcoin.window_end, False)
    assert trade.state == PaperState.EXPIRED
    assert trade.failure_reason == "BTC_WINDOW_UNAVAILABLE_AT_FILL"


@pytest.mark.parametrize("scenario", ["delayed_pair", "partial_second_leg", "second_leg_rejected"])
@pytest.mark.parametrize("fractional", [False, True])
def test_btc_shadow_delay_partial_fill_and_unwind_loss(btc, scenario, fractional):
    a, b, books, at, risk = synthetic(btc)
    signal = diagnose(match_markets(a, b), a, b, books, risk, profiles(), at, Exposure())[0]
    assert signal.shadow_qualified
    calc = signal.calculation
    due = at + timedelta(milliseconds=risk.latency_ms)
    payload = {
        "id": "synthetic-btc-shadow",
        "validation": signal.model_dump(mode="json"),
        "risk": risk.model_dump(mode="json"),
        "due_at": due.isoformat(),
        "started_at": at.isoformat(),
        "quantity": str(calc.quantity),
        "expected_profit": str(calc.net_profit),
        "reserved_capital": "25",
        "limit_one": str(calc.limit_one),
        "limit_two": str(calc.limit_two),
        "scenario": scenario,
        "unwind_latency_ms": 600,
    }
    assert complete_trial(payload, a, b, books, due)[0] == "SUBMITTED"
    for book in books.values():
        book.received_at = book.requested_at = book.exchange_at = due
    if fractional:
        for level in books[(a.id, signal.first_outcome)].asks:
            level.quantity = D("47.23")
    state, filled = complete_trial(payload, a, b, books, due)
    if scenario == "delayed_pair":
        assert state == "HEDGED" and D(filled["net_pnl"]) > 0
    else:
        assert state == "UNWIND_PENDING"
        later = due + timedelta(milliseconds=600)
        for book in books.values():
            book.received_at = book.requested_at = book.exchange_at = later
        state, closed = unwind_trial(filled, a, b, books, later)
        assert closed["unwind_quantity"] != "0"
        if scenario == "second_leg_rejected":
            assert state == "UNWOUND" and D(closed["net_pnl"]) < 0
        else:
            assert state == "HEDGED_AFTER_UNWIND"


@pytest.mark.parametrize("reference", [True, 85000.0, "NaN", "-1"])
def test_invalid_financial_reference_is_rejected(reference):
    start = datetime(2030, 1, 1, 14, tzinfo=UTC)
    with pytest.raises(ValidationError):
        BitcoinWindow(
            window_start=start,
            window_end=start + timedelta(minutes=15),
            opening_reference=reference,
        )


async def test_book_updates_compare_instant_not_variable_iso_text(store, btc):
    a, b, books, at, _ = synthetic(btc)
    for market in (a, b):
        await store.save_market(market)
    await store.save_books(list(books.values()))
    later = at + timedelta(microseconds=1)
    for book in books.values():
        book.received_at = later
    await store.save_books(list(books.values()))
    assert all(book.received_at == later for book in (await store.books([a.id, b.id])).values())
    for book in books.values():
        book.received_at = at
    await store.save_books(list(books.values()))
    assert all(book.received_at == later for book in (await store.books([a.id, b.id])).values())


def test_fractional_entry_grid_is_explicit_and_bounded(btc):
    a, b, books, at, risk = synthetic(btc)
    assert risk.entry_quantity_step == 1 and a.quantity_step == b.quantity_step == D("0.01")
    risk.max_contracts = 21
    for book in books.values():
        for level in book.asks + book.bids:
            level.quantity = D("20.25")
    ops, failures = detect(match_markets(a, b), a, b, books, risk, at)
    assert not failures and any(op.calculation.quantity == 20 for op in ops)
    risk.entry_quantity_step = D("0.01")
    ops, failures = detect(match_markets(a, b), a, b, books, risk, at)
    assert not failures and any(op.calculation.quantity == D("20.25") for op in ops)
    risk.max_contracts = 200
    for book in books.values():
        for level in book.asks + book.bids:
            level.quantity = D("200")
    ops, failures = detect(match_markets(a, b), a, b, books, risk, at)
    assert not ops and failures == ["SIZING_GRID_TOO_LARGE"]
    values = diagnose(match_markets(a, b), a, b, books, risk, profiles(), at, Exposure())
    assert "SIZING_GRID_TOO_LARGE" in values[0].reasons
    assert "INSUFFICIENT_DEPTH_OR_CAPITAL" not in values[0].reasons


def test_non_nested_entry_grid_fails_closed(btc):
    a, b, books, at, risk = synthetic(btc)
    a.quantity_step = D("1")
    risk.entry_quantity_step = D("0.6")
    ops, failures = detect(match_markets(a, b), a, b, books, risk, at)
    assert not ops and failures == ["QUANTITY_ROUNDING_MISMATCH"]


def test_invalid_identity_cannot_claim_exceptional_scenario_bounds(btc):
    a, b, *_ = synthetic(btc)
    b.bitcoin.opening_reference += D("0.01")
    proof = settlement_proof(a, b)
    assert not proof["proven"]
    assert all(not row["covered"] for row in proof["scenarios"])
    assert all(
        bound["known_scenario_floor"] is None for bound in proof["direction_bounds"].values()
    )


async def test_kalshi_depth_reconstruction_yields_to_heartbeats(btc, monkeypatch):
    from app.venues import streams

    a, _, books, at, _ = btc
    entered, heartbeat = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()

    class Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def send(self, _):
            pass

        async def recv(self):
            return "{}"

    def apply(_, data, received_at, publish):
        assert data == {} and received_at == at
        assert not publish
        loop.call_soon_threadsafe(entered.set)
        assert heartbeat.wait(2), "Book reconstruction blocked the event loop"
        return [book for book in books.values() if book.market_id == a.id]

    async def mark_heartbeat():
        await entered.wait()
        heartbeat.set()

    monkeypatch.setattr(streams, "connect", lambda *_, **__: Socket())
    monkeypatch.setattr(streams, "kalshi_headers", lambda *_: {})
    monkeypatch.setattr(streams, "now", lambda: at)
    monkeypatch.setattr(streams.KalshiStreamState, "apply", apply)
    worker = asyncio.create_task(mark_heartbeat())
    iterator = streams.kalshi_stream(Settings(_env_file=None), [a])
    try:
        assert len(await anext(iterator)) == 2
        await worker
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        await iterator.aclose()


def test_deferred_kalshi_depth_keeps_every_delta_and_original_receipt(btc):
    from app.venues.streams import KalshiStreamState

    a, _, _, at, _ = btc
    state = KalshiStreamState([a])
    snapshot = {
        "type": "orderbook_snapshot",
        "sid": 1,
        "seq": 1,
        "msg": {
            "market_ticker": a.external_id,
            "yes_dollars_fp": [["0.44", "100"]],
            "no_dollars_fp": [["0.55", "100"]],
        },
    }
    assert state.apply(snapshot, at, False) == []
    later = at + timedelta(milliseconds=1)
    delta = {
        "type": "orderbook_delta",
        "sid": 1,
        "seq": 2,
        "msg": {
            "market_ticker": a.external_id,
            "side": "yes",
            "price_dollars": "0.44",
            "delta_fp": "0.25",
        },
    }
    assert state.apply(delta, later, False) == []
    first, second = state.flush()
    assert first.bids[0].quantity == D("100.25")
    assert first.sequence == second.sequence == 2
    assert first.received_at == second.received_at == later
    assert not state.pending and state.flush() == []
    removal = {**delta, "seq": 3, "msg": {**delta["msg"], "delta_fp": "-100.25"}}
    assert state.apply(removal, later, False) == []
    assert not state.flush()[0].bids
    with pytest.raises(BookIntegrityError, match="SEQUENCE_GAP"):
        state.apply({**delta, "seq": 5}, later, False)


async def test_kalshi_flushes_last_delta_when_the_feed_becomes_quiet(btc, monkeypatch):
    from app.venues import streams

    a, _, _, at, _ = btc
    messages = [
        {
            "type": "orderbook_snapshot",
            "sid": 1,
            "seq": 1,
            "msg": {
                "market_ticker": a.external_id,
                "yes_dollars_fp": [["0.44", "100"]],
                "no_dollars_fp": [["0.55", "100"]],
            },
        },
        {
            "type": "orderbook_delta",
            "sid": 1,
            "seq": 2,
            "msg": {
                "market_ticker": a.external_id,
                "side": "yes",
                "price_dollars": "0.44",
                "delta_fp": "0.25",
            },
        },
    ]

    class Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def send(self, _):
            pass

        async def recv(self):
            if messages:
                return json.dumps(messages.pop(0))
            await asyncio.Event().wait()
            raise AssertionError("Quiet fake socket unexpectedly resumed")

    monkeypatch.setattr(streams, "connect", lambda *_, **__: Socket())
    monkeypatch.setattr(streams, "kalshi_headers", lambda *_: {})
    monkeypatch.setattr(streams, "now", lambda: at)
    iterator = streams.kalshi_stream(Settings(_env_file=None), [a])
    try:
        initial = await anext(iterator)
        assert initial[0].bids[0].quantity == 100
        updated = await asyncio.wait_for(anext(iterator), timeout=2)
        assert updated[0].bids[0].quantity == D("100.25")
        assert updated[0].sequence == 2 and updated[0].received_at == at
    finally:
        await iterator.aclose()

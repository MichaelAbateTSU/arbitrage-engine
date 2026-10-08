from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

import httpx
import pytest
import respx
from pydantic import ValidationError

from app.config import Settings
from app.demo import demo_markets
from app.domain import D, now
from app.us500 import (
    TICKER,
    AccountEvidence,
    CarryPoint,
    CarryScenario,
    FundingEstimate,
    FundingEvent,
    LinearLevel,
    MarginBook,
    MarginMarket,
    carry_analysis,
    diagnose_us500,
    stress_cases,
)
from app.venues.clients import KalshiClient
from app.venues.http import PublicHTTP, VenueError, decode
from app.venues.mapper import kalshi_market
from app.venues.perpetuals import BASE, MarginResearchClient, read_account_evidence
from app.venues.schemas import KalshiMarket


@pytest.fixture
def market_payload():
    return {
        "ticker": TICKER,
        "status": "active",
        "title": "US500",
        "contract_size": "0.001000",
        "underlying_multiplier": "1.000000",
        "tick_size": "0.0001",
        "fractional_trading_enabled": False,
        "schedule": {"is_open": True, "next_close_ts": None, "next_open_ts": None},
        "reference_price": {"price": "13.7288", "ts_ms": int(now().timestamp() * 1000)},
    }


@pytest.fixture
def book():
    instant = now()
    return MarginBook(
        bids=[
            LinearLevel(price="13.7249", quantity="10"),
            LinearLevel(price="13.7239", quantity="10"),
        ],
        asks=[
            LinearLevel(price="13.7264", quantity="10"),
            LinearLevel(price="13.7274", quantity="10"),
        ],
        requested_at=instant,
        received_at=instant,
    )


def funding_estimate():
    return FundingEstimate(
        market_ticker=TICKER,
        computed_time=now(),
        funding_rate="0",
        mark_price="13.7255",
        next_funding_time=now() + timedelta(hours=1),
    )


def scenario(**overrides):
    values = {
        "label": "synthetic",
        "direction": "short_perp_long_hedge",
        "quantity": "100",
        "entry_price": "10",
        "exit_price": "10",
        "entry_fee_rate": "0.0001",
        "exit_fee_rate": "0.0001",
        "hedge_pnl": "0",
        "hedge_trade_cost": "0.10",
        "financing_cost": "0.10",
        "borrow_cost": "0",
        "dividend_income": "0",
        "additional_cost": "0",
        "venue_collateral": "1000",
        "points": [
            CarryPoint(
                mark_price="10",
                funding_settlement_price="10",
                funding_rate="0.001",
                maintenance_requirement="20",
            )
        ],
    }
    values.update(overrides)
    return CarryScenario(**values)


def test_linear_depth_uses_real_dollars_not_binary_complements(market_payload, book):
    market = MarginMarket.model_validate(market_payload)
    result = diagnose_us500(market, book, funding_estimate(), [], D("15"), now())
    assert result["purchase_cost"] == "205.9010"
    assert result["sale_proceeds"] == "205.8685"
    assert result["same_snapshot_round_trip_gross"] == "-0.0325"
    assert result["underlying_units"] == "0.015000000000"
    assert result["arbitrage_qualified"] is False
    assert result["net_after_costs"] is None
    assert result["book_provider_timestamp"] is None
    assert "ORDERBOOK_PROVIDER_TIMESTAMP_UNAVAILABLE" in result["reasons"]


@pytest.mark.parametrize("invalid", ["0", "-1", "1.01", "NaN", "Infinity"])
def test_market_quantity_respects_current_metadata(market_payload, invalid):
    market = MarginMarket.model_validate(market_payload)
    assert not market.accepts_quantity(D(invalid))


def test_api_fractional_flag_not_filing_minimum_controls_quantity(market_payload):
    market_payload["fractional_trading_enabled"] = True
    market = MarginMarket.model_validate(market_payload)
    assert market.accepts_quantity(D("0.01"))
    assert not market.accepts_quantity(D("0.0001"))


@pytest.mark.parametrize("value", [0.001, True, "NaN", "Infinity"])
def test_exact_funding_rates_reject_float_bool_nonfinite(value):
    with pytest.raises(ValidationError):
        FundingEvent(
            market_ticker=TICKER,
            funding_time=now(),
            funding_rate=value,
            mark_price="13.7260",
        )


def test_numeric_json_funding_retains_full_precision():
    payload = decode(
        '{"market_ticker":"KXUS500PERP","funding_time":"2026-10-07T20:00:00Z",'
        '"funding_rate":0.0010869999556274,"mark_price":"13.7260"}'
    )
    event = FundingEvent.model_validate(payload)
    assert event.funding_rate == Decimal("0.0010869999556274")
    assert D("10000") * event.funding_rate == D("10.8699995562740000")


def test_applied_funding_not_annually_projected_or_counted_twice(market_payload, book):
    event = FundingEvent(
        market_ticker=TICKER,
        funding_time=now() - timedelta(days=1),
        funding_rate="0.001",
        mark_price="13.7260",
    )
    result = diagnose_us500(
        MarginMarket.model_validate(market_payload),
        book,
        funding_estimate(),
        [event],
        D("15"),
        now(),
        AccountEvidence(observed_at=now(), enabled=False),
    )
    assert result["applied_funding"][0]["short_gross_payment_at_quantity"] == "0.2058900"
    assert "PERPS_ACCOUNT_DISABLED" in result["reasons"]
    assert result["provisional_short_funding"] == "0.0000"
    assert result["net_after_costs"] is None
    with pytest.raises(ValueError, match="DUPLICATE"):
        diagnose_us500(
            MarginMarket.model_validate(market_payload),
            book,
            funding_estimate(),
            [event, event],
            D("15"),
            now(),
        )


def test_expected_overnight_reference_is_not_fresh_executable_spot(market_payload, book):
    market_payload["reference_price"]["ts_ms"] -= 9 * 3600 * 1000
    result = diagnose_us500(
        MarginMarket.model_validate(market_payload), book, funding_estimate(), [], D("15"), now()
    )
    assert "REFERENCE_NOT_CURRENT_NOT_AN_EXECUTABLE_HEDGE" in result["reasons"]
    assert result["reference_is_executable_hedge"] is False


def test_empty_depth_stale_request_closed_schedule_fail_closed(market_payload, book):
    market_payload["schedule"]["is_open"] = False
    book.bids = []
    book.requested_at -= timedelta(seconds=10)
    result = diagnose_us500(
        MarginMarket.model_validate(market_payload), book, funding_estimate(), [], D("15"), now()
    )
    assert {
        "INSUFFICIENT_TWO_SIDED_DEPTH",
        "ORDERBOOK_REQUEST_STALE",
        "MARGIN_MARKET_CLOSED",
    } <= set(result["reasons"])
    assert result["same_snapshot_round_trip_gross"] is None


def test_future_mark_timestamp_rejected(market_payload, book):
    market_payload["settlement_mark_price"] = {
        "price": "13.7",
        "ts_ms": int((now() + timedelta(seconds=10)).timestamp() * 1000),
    }
    with pytest.raises(ValueError, match="FUTURE_PROVIDER_TIMESTAMP"):
        diagnose_us500(
            MarginMarket.model_validate(market_payload),
            book,
            funding_estimate(),
            [],
            D("15"),
            now(),
        )


def test_off_tick_or_wrong_quantity_cannot_be_priced(market_payload, book):
    book.asks[0].price = D("13.72641")
    with pytest.raises(ValueError, match="GRID"):
        diagnose_us500(
            MarginMarket.model_validate(market_payload),
            book,
            funding_estimate(),
            [],
            D("15"),
            now(),
        )


@pytest.mark.parametrize(
    "cost_field",
    [
        "entry_fee_rate",
        "exit_fee_rate",
        "hedge_trade_cost",
        "financing_cost",
        "borrow_cost",
        "dividend_income",
        "additional_cost",
    ],
)
def test_unknown_cost_is_not_zero_profit(cost_field):
    result = carry_analysis(scenario(**{cost_field: None}))
    assert result["net_pnl_if_held_to_exit"] is None
    assert result["minimum_net_funding_cashflow"] is None
    assert result["costs_complete"] is False


def test_funding_signs_costs_and_breakeven():
    short = carry_analysis(scenario())
    assert D(short["funding_pnl"]) == 1
    assert D(short["estimated_trade_fees"]) == D("0.20")
    assert D(short["minimum_net_funding_cashflow"]) == D("0.40")
    assert D(short["net_pnl_if_held_to_exit"]) == D("0.60")
    assert short["arbitrage_qualified"] is False
    long = carry_analysis(scenario(direction="long_perp_short_hedge"))
    assert D(long["funding_pnl"]) == -1
    assert D(long["net_pnl_if_held_to_exit"]) == D("-1.40")


def test_funding_reversal_loss_and_explicit_borrow_dividends():
    value = scenario(direction="long_perp_short_hedge", borrow_cost="0.50", dividend_income="-0.20")
    value.points[0].funding_rate = D("-0.001")
    result = carry_analysis(value)
    assert D(result["funding_pnl"]) == 1
    assert D(result["net_pnl_if_held_to_exit"]) == D("-0.10")


def test_other_venue_hedge_gains_do_not_prevent_liquidation():
    value = scenario(
        exit_price="10.5",
        hedge_pnl="50",
        venue_collateral="40",
        points=[
            CarryPoint(
                mark_price="10.5",
                funding_settlement_price="10.5",
                funding_rate="0.002",
                maintenance_requirement="20",
            )
        ],
    )
    result = carry_analysis(value)
    assert D(result["net_pnl_if_held_to_exit"]) > 0
    assert D(result["minimum_venue_equity"]) < 0
    assert result["margin_breached"] is True
    assert result["modeled_exit_survives_margin_path"] is False
    assert result["arbitrage_qualified"] is False


def test_pending_funding_cannot_rescue_already_breached_margin():
    value = scenario(
        exit_price="10.5",
        hedge_pnl="50",
        venue_collateral="60",
        points=[
            CarryPoint(
                mark_price="10.5",
                funding_settlement_price="10.5",
                funding_rate="0.02",
                maintenance_requirement="20",
            )
        ],
    )
    assert carry_analysis(value)["margin_breached"] is True


def test_synthetic_stress_is_never_live_profit():
    values = {value["label"]: value for value in stress_cases(D("13.7255"), D("1000"))}
    assert D(values["positive_funding"]["net_pnl_if_held_to_exit"]) > 0
    assert D(values["funding_reversal"]["net_pnl_if_held_to_exit"]) < 0
    assert D(values["zero_funding"]["net_pnl_if_held_to_exit"]) < 0
    assert D(values["basis_widens_or_hedge_fails"]["net_pnl_if_held_to_exit"]) < 0
    assert values["neutral_portfolio_venue_liquidation"]["margin_breached"] is True
    assert all(value["evidence_mode"] == "synthetic_stress" for value in values.values())
    assert all(not value["arbitrage_qualified"] for value in values.values())


@pytest.mark.parametrize("market_type", ["perpetual", "linear", "unsupported", "binary"])
def test_nonbinary_products_cannot_enter_binary_mapper(market_type):
    wire = KalshiMarket(
        ticker=TICKER, event_ticker=TICKER, title="US500", status="active", market_type=market_type
    )
    with pytest.raises(VenueError, match="NON_BINARY"):
        kalshi_market(wire, "UNKNOWN", {}, D("0"), [])


@pytest.mark.parametrize("market_type", ["unsupported", "moneyline"])
async def test_nonbinary_product_cannot_use_complementary_book_adapter(market_type):
    market = demo_markets()[0]
    market.market_type = market_type
    market.external_id = TICKER
    async with httpx.AsyncClient() as http:
        with pytest.raises(VenueError, match="NON_BINARY"):
            await KalshiClient(PublicHTTP(http), Settings()).get_orderbooks(market)


async def test_public_api_reorders_real_wire_depth_without_fake_timestamp(market_payload):
    instant = now()
    with respx.mock as routes:
        routes.get(f"{BASE}/markets/{TICKER}").respond(200, json={"market": market_payload})
        routes.get(f"{BASE}/markets/{TICKER}/orderbook").respond(
            200,
            json={
                "orderbook": {
                    "bids": [["13.7239", "10"], ["13.7249", "10"]],
                    "asks": [["13.7274", "10"], ["13.7264", "10"]],
                }
            },
        )
        routes.get(f"{BASE}/funding_rates/estimate").respond(
            200, json=funding_estimate().model_dump(mode="json")
        )
        routes.get(f"{BASE}/funding_rates/historical").respond(200, json={"funding_rates": []})
        async with httpx.AsyncClient() as http:
            result = await MarginResearchClient(PublicHTTP(http, 1000)).sample(D("15"))
        assert all(call.request.method == "GET" for call in routes.calls)
        assert all("KALSHI-ACCESS-KEY" not in call.request.headers for call in routes.calls)
        assert all("/margin/" in str(call.request.url) for call in routes.calls)
    assert result["book_provider_timestamp"] is None
    assert datetime.fromisoformat(result["book_received_at"]) >= instant
    assert result["purchase_cost"] == "205.9010"


@pytest.mark.parametrize("kind", ["crossed", "duplicates", "negative", "float", "wrong_shape"])
def test_invalid_depth_fails_closed(kind):
    values = {
        "crossed": {"bids": [["14", "1"]], "asks": [["13", "1"]]},
        "duplicates": {"bids": [["13", "1"], ["13", "2"]], "asks": []},
        "negative": {"bids": [["13", "-1"]], "asks": []},
        "float": {"bids": [[13.0, "1"]], "asks": []},
        "wrong_shape": {"bids": [["13", "1", "extra"]], "asks": []},
    }[kind]
    with pytest.raises(ValueError):
        MarginBook(
            bids=MarginResearchClient._levels(values["bids"], True),
            asks=MarginResearchClient._levels(values["asks"], False),
            requested_at=now(),
            received_at=now(),
        )


async def test_account_disabled_does_not_probe_fees_or_enable_anything(monkeypatch):
    signer = Mock(return_value={"X-Test-Signature": "test-only"})
    monkeypatch.setattr("app.venues.perpetuals.kalshi_headers", signer)
    with respx.mock as routes:
        routes.get(f"{BASE}/enabled").respond(200, json={"enabled": False})
        async with httpx.AsyncClient() as http:
            result = await read_account_evidence(http, Settings(_env_file=None))
        assert len(routes.calls) == 1
        assert routes.calls[0].request.method == "GET"
    assert result.enabled is False and result.taker_fee_rate is None
    assert signer.call_args.args[1] == "/trade-api/v2/margin/enabled"


async def test_account_fees_are_exact_and_filtered_to_requested_product(monkeypatch):
    monkeypatch.setattr("app.venues.perpetuals.kalshi_headers", lambda *_: {})
    with respx.mock as routes:
        routes.get(f"{BASE}/enabled").respond(200, json={"enabled": True})
        routes.get(f"{BASE}/fee_tiers").respond(
            200, text='{"taker_fee_rates":{"KXUS500PERP":0.00055,"OTHER":0.02}}'
        )
        async with httpx.AsyncClient() as http:
            result = await read_account_evidence(http, Settings(_env_file=None))
    assert result.taker_fee_rate == D("0.00055")
    assert "OTHER" not in result.model_dump_json()


async def test_account_http_denial_is_not_enabled_or_zero_fees(monkeypatch):
    monkeypatch.setattr("app.venues.perpetuals.kalshi_headers", lambda *_: {})
    with respx.mock:
        respx.get(f"{BASE}/enabled").respond(403)
        async with httpx.AsyncClient() as http:
            result = await read_account_evidence(http, Settings(_env_file=None))
    assert result.enabled is None and result.taker_fee_rate is None
    assert result.enabled_http_status == 403
    assert result.error_code == "MARGIN_ACCOUNT_EVIDENCE_HTTP_ERROR"

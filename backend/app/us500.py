"""Separate linear-product research. Nothing here qualifies or executes binary arbitrage."""

from collections.abc import Sequence
from datetime import datetime
from decimal import ROUND_CEILING, Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    model_validator,
)

from app.domain import D, Model
from app.domain import exact_decimal as exact
from app.pricing import cost, depth_slices, quantity

TICKER: Literal["KXUS500PERP"] = "KXUS500PERP"
FILING_URL = "https://www.cftc.gov/filings/ptc/ptc08182617972.pdf"
FILING_SHA256 = "81d77bf2996bada00a37dedd2748e37641fb0c2c5fa11c16bec6a17fa9934be6"


Exact = Annotated[Decimal, BeforeValidator(exact)]
Positive = Annotated[Exact, Field(gt=0, max_digits=24, decimal_places=8)]
Nonnegative = Annotated[Exact, Field(ge=0, max_digits=24, decimal_places=8)]
Rate = Annotated[Exact, Field(ge=-1, le=1)]
FeeRate = Annotated[Exact, Field(ge=0, le=1)]


class PublicWire(Model):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)


class TimestampedPrice(PublicWire):
    price: Positive
    ts_ms: int = Field(ge=0, strict=True)


class TradingSchedule(PublicWire):
    is_open: StrictBool
    next_close_ts: int | None
    next_open_ts: int | None


class MarginMarket(PublicWire):
    ticker: Literal["KXUS500PERP"]
    status: Literal["active", "inactive", "closed"]
    title: str
    contract_size: Positive
    underlying_multiplier: Positive
    tick_size: Positive
    fractional_trading_enabled: StrictBool
    schedule: TradingSchedule | None
    market_version: int | None = Field(default=None, ge=1)
    reference_price: TimestampedPrice | None = None
    settlement_mark_price: TimestampedPrice | None = None
    liquidation_mark_price: TimestampedPrice | None = None

    @property
    def quantity_step(self) -> Decimal:
        return D("0.01") if self.fractional_trading_enabled else D("1")

    def accepts_quantity(self, value: Decimal) -> bool:
        return value.is_finite() and value > 0 and value % self.quantity_step == 0


class LinearLevel(Model):
    price: Positive
    quantity: Positive


class MarginBook(Model):
    ticker: Literal["KXUS500PERP"] = TICKER
    bids: list[LinearLevel]
    asks: list[LinearLevel]
    requested_at: AwareDatetime
    received_at: AwareDatetime
    provider_timestamp: AwareDatetime | None = None

    @model_validator(mode="after")
    def integrity(self) -> "MarginBook":
        if self.requested_at > self.received_at:
            raise ValueError("INVALID_BOOK_RECEIPT_ORDER")
        for levels, descending in ((self.bids, True), (self.asks, False)):
            prices = [level.price for level in levels]
            if len(prices) != len(set(prices)) or prices != sorted(prices, reverse=descending):
                raise ValueError("INVALID_LINEAR_BOOK_ORDER")
        if self.bids and self.asks and self.bids[0].price > self.asks[0].price:
            raise ValueError("CROSSED_LINEAR_BOOK")
        return self

    def walk(self, buy: bool, size: Decimal) -> list[LinearLevel]:
        levels = self.asks if buy else self.bids
        return [LinearLevel(price=p, quantity=q) for p, q in depth_slices(levels, size)]


class FundingEvent(PublicWire):
    market_ticker: Literal["KXUS500PERP"]
    funding_time: AwareDatetime
    funding_rate: Rate
    mark_price: Positive


class FundingEstimate(PublicWire):
    market_ticker: Literal["KXUS500PERP"]
    computed_time: AwareDatetime | None = None
    funding_rate: Rate | None = None
    mark_price: Positive | None = None
    next_funding_time: AwareDatetime


class AccountEvidence(Model):
    observed_at: AwareDatetime
    enabled: StrictBool | None = None
    enabled_http_status: int | None = None
    fees_http_status: int | None = None
    taker_fee_rate: FeeRate | None = None
    error_code: str | None = None


class CarryPoint(Model):
    mark_price: Positive
    funding_settlement_price: Positive
    funding_rate: Rate
    maintenance_requirement: Nonnegative


class CarryScenario(Model):
    label: str
    evidence_mode: Literal["synthetic_stress"] = "synthetic_stress"
    direction: Literal["short_perp_long_hedge", "long_perp_short_hedge"]
    quantity: Positive
    entry_price: Positive
    exit_price: Positive
    entry_fee_rate: FeeRate | None = None
    exit_fee_rate: FeeRate | None = None
    hedge_pnl: Exact
    hedge_trade_cost: Nonnegative | None = None
    financing_cost: Nonnegative | None = None
    borrow_cost: Nonnegative | None = None
    dividend_income: Exact | None = None
    additional_cost: Nonnegative | None = None
    venue_collateral: Nonnegative | None = None
    points: list[CarryPoint] = Field(min_length=1, max_length=1000)


def carry_analysis(scenario: CarryScenario) -> dict[str, Any]:
    """Conditional cashflows; hedge gains at another venue cannot fund Kalshi margin."""
    sign = D("1") if scenario.direction == "short_perp_long_hedge" else D("-1")
    size = scenario.quantity
    price_pnl = sign * size * (scenario.entry_price - scenario.exit_price)
    funding = sum(
        (
            sign * size * point.funding_settlement_price * point.funding_rate
            for point in scenario.points
        ),
        D("0"),
    )
    fees = None
    entry_fee = None
    if scenario.entry_fee_rate is not None:
        # Conservative per-leg cent ceilings, not a claim about exact provider rounding.
        entry_fee = (size * scenario.entry_price * scenario.entry_fee_rate).quantize(
            D("0.01"), rounding=ROUND_CEILING
        )
    if entry_fee is not None and scenario.exit_fee_rate is not None:
        fees = entry_fee + (size * scenario.exit_price * scenario.exit_fee_rate).quantize(
            D("0.01"), rounding=ROUND_CEILING
        )
    costs = (
        fees,
        scenario.hedge_trade_cost,
        scenario.financing_cost,
        scenario.borrow_cost,
        scenario.additional_cost,
    )
    net = None
    minimum_funding = None
    if all(value is not None for value in costs) and scenario.dividend_income is not None:
        minimum_funding = (
            sum((value for value in costs if value is not None), D("0"))
            - price_pnl
            - scenario.hedge_pnl
            - scenario.dividend_income
        )
        net = (
            price_pnl
            + funding
            + scenario.hedge_pnl
            + scenario.dividend_income
            - sum((value for value in costs if value is not None), D("0"))
        )
    equities: list[Decimal] = []
    margin_breached: bool | None = None
    if scenario.venue_collateral is not None and entry_fee is not None:
        running_funding = D("0")
        initial_equity = scenario.venue_collateral - entry_fee
        equities.append(initial_equity)
        margin_breached = initial_equity <= 0
        for point in scenario.points:
            before_funding = (
                scenario.venue_collateral
                - entry_fee
                + sign * size * (scenario.entry_price - point.mark_price)
                + running_funding
            )
            running_funding += sign * size * point.funding_settlement_price * point.funding_rate
            after_funding = (
                before_funding + sign * size * point.funding_settlement_price * point.funding_rate
            )
            equities.extend((before_funding, after_funding))
            margin_breached = margin_breached or (
                min(before_funding, after_funding) <= point.maintenance_requirement
            )
    return {
        "label": scenario.label,
        "evidence_mode": scenario.evidence_mode,
        "strategy": "risk_bearing_funding_carry",
        "perpetual_price_pnl": str(price_pnl),
        "funding_pnl": str(funding),
        "hedge_pnl": str(scenario.hedge_pnl),
        "estimated_trade_fees": str(fees) if fees is not None else None,
        "net_pnl_if_held_to_exit": str(net) if net is not None else None,
        "minimum_net_funding_cashflow": str(minimum_funding)
        if minimum_funding is not None
        else None,
        "costs_complete": net is not None,
        "minimum_venue_equity": str(min(equities)) if equities else None,
        "margin_breached": margin_breached,
        "modeled_exit_survives_margin_path": False if margin_breached else None,
        "arbitrage_qualified": False,
        "orders_submitted": False,
    }


def stress_cases(entry_price: Decimal, size: Decimal) -> list[dict[str, Any]]:
    """Illustrative assumptions, never inferred account fees, fills or future funding."""
    notional = entry_price * size
    results = []
    for label, rate, shock, matched_hedge in (
        ("positive_funding", "0.002", "0", True),
        ("funding_reversal", "-0.001", "0", True),
        ("zero_funding", "0", "0", True),
        ("basis_widens_or_hedge_fails", "0.001", "0.05", False),
        ("neutral_portfolio_venue_liquidation", "0", "0.05", True),
    ):
        exit_price = entry_price * (1 + D(shock))
        scenario = CarryScenario(
            label=label,
            direction="short_perp_long_hedge",
            quantity=size,
            entry_price=entry_price,
            exit_price=exit_price,
            entry_fee_rate=D("0.0005"),
            exit_fee_rate=D("0.0005"),
            hedge_pnl=size * (exit_price - entry_price) if matched_hedge else D("0"),
            hedge_trade_cost=notional * D("0.0002"),
            financing_cost=notional * D("0.0001"),
            borrow_cost=D("0"),
            dividend_income=D("0"),
            additional_cost=D("0"),
            venue_collateral=notional * D("0.05"),
            points=[
                CarryPoint(
                    mark_price=exit_price,
                    funding_settlement_price=exit_price,
                    funding_rate=D(rate),
                    maintenance_requirement=notional * D("0.02"),
                )
            ],
        )
        result = carry_analysis(scenario)
        result["assumptions"] = scenario.model_dump(mode="json")
        results.append(result)
    return results


def diagnose_us500(
    market: MarginMarket,
    book: MarginBook,
    estimate: FundingEstimate,
    history: Sequence[FundingEvent],
    size: Decimal,
    at: datetime,
    account: AccountEvidence | None = None,
) -> dict[str, Any]:
    if not market.accepts_quantity(size):
        raise ValueError("INVALID_MARGIN_QUANTITY")
    if at.tzinfo is None:
        raise ValueError("TIMEZONE_REQUIRED")
    if any(
        level.price % market.tick_size or level.quantity % market.quantity_step
        for level in book.bids + book.asks
    ):
        raise ValueError("INVALID_MARGIN_PRICE_OR_QUANTITY_GRID")
    times = [book.requested_at, book.received_at]
    if book.provider_timestamp:
        times.append(book.provider_timestamp)
    if estimate.computed_time:
        times.append(estimate.computed_time)
    if any((timestamp - at).total_seconds() > 1 for timestamp in times):
        raise ValueError("FUTURE_PROVIDER_TIMESTAMP")
    if any(
        price and price.ts_ms > int(at.timestamp() * 1000) + 1000
        for price in (
            market.reference_price,
            market.settlement_mark_price,
            market.liquidation_mark_price,
        )
    ):
        raise ValueError("FUTURE_PROVIDER_TIMESTAMP")
    if any(event.funding_time > at for event in history):
        raise ValueError("FUTURE_APPLIED_FUNDING")
    if len({event.funding_time for event in history}) != len(history):
        raise ValueError("DUPLICATE_APPLIED_FUNDING")
    reasons = [
        "NO_EXPIRY_CONVERGENCE_GUARANTEE",
        "EXECUTABLE_EXACT_INDEX_HEDGE_UNVERIFIED",
        "FUTURE_FUNDING_NOT_LOCKED",
        "ADDITIONAL_COSTS_UNVERIFIED",
        "MARGIN_AND_LIQUIDATION_PATH_UNVERIFIED",
        "CURRENT_GOVERNING_TERMS_UNBOUND",
    ]
    if not account or account.enabled is None:
        reasons.append("PERPS_ACCOUNT_PERMISSION_UNVERIFIED")
    elif not account.enabled:
        reasons.append("PERPS_ACCOUNT_DISABLED")
    if not account or account.taker_fee_rate is None:
        reasons.append("ACCOUNT_PERPS_FEES_UNVERIFIED")
    if market.status != "active" or (market.schedule and not market.schedule.is_open):
        reasons.append("MARGIN_MARKET_CLOSED")
    if book.provider_timestamp is None:
        reasons.append("ORDERBOOK_PROVIDER_TIMESTAMP_UNAVAILABLE")
    if (at - book.requested_at).total_seconds() > 5:
        reasons.append("ORDERBOOK_REQUEST_STALE")
    if estimate.computed_time is None or (at - estimate.computed_time).total_seconds() > 5:
        reasons.append("FUNDING_ESTIMATE_STALE_OR_UNDATED")
    if estimate.next_funding_time <= at:
        reasons.append("FUNDING_PERIOD_EXPIRED")
    asks, bids = book.walk(True, size), book.walk(False, size)
    full_depth = quantity(asks) == size and quantity(bids) == size
    if not full_depth:
        reasons.append("INSUFFICIENT_TWO_SIDED_DEPTH")
    reference_age = None
    if market.reference_price:
        reference_age = int(at.timestamp() * 1000) - market.reference_price.ts_ms
        if reference_age < -1000:
            raise ValueError("FUTURE_REFERENCE_TIMESTAMP")
        if reference_age > 5000:
            reasons.append("REFERENCE_NOT_CURRENT_NOT_AN_EXECUTABLE_HEDGE")
    projected_short_funding = None
    if estimate.mark_price is not None and estimate.funding_rate is not None:
        projected_short_funding = size * estimate.mark_price * estimate.funding_rate
    return {
        "ticker": TICKER,
        "product": "perpetual_future",
        "strategy": "risk_bearing_funding_carry",
        "source": "public",
        "sampled_at": at.isoformat(),
        "quantity_api_contracts": str(size),
        "api_contract_size": str(market.contract_size),
        "underlying_units": str(size * market.contract_size * market.underlying_multiplier),
        "quantity_step_api_contracts": str(market.quantity_step),
        "tick_size_api_dollars": str(market.tick_size),
        "purchase_cost": str(cost(asks)) if full_depth else None,
        "sale_proceeds": str(cost(bids)) if full_depth else None,
        "same_snapshot_round_trip_gross": str(cost(bids) - cost(asks)) if full_depth else None,
        "book_requested_at": book.requested_at.isoformat(),
        "book_received_at": book.received_at.isoformat(),
        "book_provider_timestamp": book.provider_timestamp,
        "reference_price": market.reference_price.model_dump(mode="json")
        if market.reference_price
        else None,
        "reference_age_ms": reference_age,
        "reference_is_executable_hedge": False,
        "next_funding_estimate": estimate.model_dump(mode="json"),
        "provisional_short_funding": str(projected_short_funding)
        if projected_short_funding is not None
        else None,
        "applied_funding": [
            {
                **event.model_dump(mode="json"),
                "short_gross_payment_at_quantity": str(
                    size * event.mark_price * event.funding_rate
                ),
                "short_gross_per_10000_notional": str(D("10000") * event.funding_rate),
            }
            for event in sorted(history, key=lambda event: event.funding_time)
        ],
        "account": account.model_dump(mode="json") if account else None,
        "net_after_costs": None,
        "reasons": reasons,
        "arbitrage_qualified": False,
        "orders_submitted": False,
    }

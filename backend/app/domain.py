import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

D = Decimal
Positive = Annotated[Decimal, Field(gt=0, max_digits=24, decimal_places=8)]
Nonnegative = Annotated[Decimal, Field(ge=0, max_digits=24, decimal_places=8)]
Amount = Annotated[Decimal, Field(ge=0)]
Price = Annotated[Decimal, Field(ge=0, le=1)]


def now() -> datetime:
    return datetime.now(UTC)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


class Venue(StrEnum):
    KALSHI = "kalshi"
    US = "polymarket_us"
    INTERNATIONAL = "polymarket_international"


class Side(StrEnum):
    YES = "YES"
    NO = "NO"

    @property
    def opposite(self) -> "Side":
        return Side.NO if self == Side.YES else Side.YES


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    @field_validator("*", mode="before")
    @classmethod
    def no_nonfinite_or_float_money(cls, value: Any, info: Any) -> Any:
        field = cls.model_fields.get(info.field_name)
        if field and field.annotation is Decimal:
            if isinstance(value, float):
                raise ValueError("Financial values must be decimal strings, not floats")
            if not D(value).is_finite():
                raise ValueError("Financial values must be finite")
        return value


class PriceRange(Model):
    start: Price
    end: Price
    step: Positive

    def accepts(self, price: Decimal) -> bool:
        return self.start <= price <= self.end and (price - self.start) % self.step == 0


class Rules(Model):
    period: str | None = None
    overtime: Literal["included", "excluded"] | None = None
    draw: Literal["impossible", "half_refund", "no"] | None = None
    cancellation: Literal["half_refund", "fair_price", "void"] | None = None
    postponement: str | None = None
    settlement_source: str | None = None
    payout: Price = D("1")
    evidence: dict[str, str] = Field(default_factory=dict)

    @property
    def unknowns(self) -> list[str]:
        fields = ("period", "overtime", "draw", "cancellation", "postponement", "settlement_source")
        return [field for field in fields if getattr(self, field) is None]


class FeeSpec(Model):
    kind: Literal["kalshi_bound", "us_quadratic", "international", "zero", "unknown"] = "unknown"
    rate: Nonnegative = D("0")
    exponent: int = Field(default=1, ge=1, le=2)
    version: str = "unknown"
    source: str = ""
    observed_at: datetime = Field(default_factory=now)
    valid_until: datetime | None = None

    def known_at(self, instant: datetime) -> bool:
        return self.kind != "unknown" and (
            self.valid_until is not None and self.observed_at <= instant <= self.valid_until
        )


class Market(Model):
    id: str
    venue: Venue
    quote_currency: Literal["USD", "USDC"] = "USD"
    external_id: str
    event_id: str
    title: str
    rules_text: str
    league: str = "UNKNOWN"
    sport: str = "UNKNOWN"
    participants: list[str] = Field(default_factory=list)
    yes_team: str | None = None
    start_time: datetime | None = None
    start_time_verified: bool = False
    market_type: str = "unknown"
    status: str = "unknown"
    tradable: bool = False
    token_ids: dict[Side, str] = Field(default_factory=dict)
    price_ranges: list[PriceRange] = Field(default_factory=list)
    quantity_step: Positive = D("1")
    minimum_quantity: Nonnegative = D("1")
    minimum_notional: Nonnegative = D("0")
    rules: Rules = Field(default_factory=Rules)
    fee: FeeSpec = Field(default_factory=FeeSpec)
    source: Literal["demo", "public"] = "public"
    raw: dict[str, Any] = Field(default_factory=dict)
    discovered_at: datetime = Field(default_factory=now)
    result: Price | None = None

    @field_validator("start_time", "discovered_at")
    @classmethod
    def aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Timezone-aware timestamps required")
        return value

    @property
    def public_spec_hash(self) -> str:
        sides = self.raw.get("marketSides") or []
        return fingerprint(
            {
                "venue": self.venue,
                "external_id": self.external_id,
                "event_id": self.event_id,
                "title": self.title,
                "rules": self.rules_text,
                "raw_start": self.raw.get("gameStartTime"),
                "raw_type": self.raw.get("sportsMarketType"),
                "raw_strike": self.raw.get("custom_strike"),
                "tokens": self.token_ids,
                "sides": [
                    {key: side.get(key) for key in ("identifier", "long", "teamId", "description")}
                    for side in sides
                ],
            }
        )

    @property
    def rules_hash(self) -> str:
        return fingerprint(
            {
                "raw": self.rules_text,
                "source_specification": self.public_spec_hash,
                "quote_currency": self.quote_currency,
                "normalized": self.rules.model_dump(mode="json"),
                "participants": self.participants,
                "yes_team": self.yes_team,
                "start_time": self.start_time,
                "verified": self.start_time_verified,
                "market_type": self.market_type,
                "league": self.league,
            }
        )

    def valid_price(self, price: Decimal, side: Side = Side.YES) -> bool:
        quote = price if side == Side.YES else D("1") - price
        return bool(self.price_ranges) and any(r.accepts(quote) for r in self.price_ranges)


class Level(Model):
    price: Price
    quantity: Positive


class Book(Model):
    market_id: str
    outcome: Side
    bids: list[Level] = Field(default_factory=list)
    asks: list[Level] = Field(default_factory=list)
    exchange_at: datetime | None = None
    requested_at: datetime | None = None
    received_at: datetime = Field(default_factory=now)
    processed_at: datetime = Field(default_factory=now)
    sequence: int | None = None
    version: int = 1
    synchronized: bool = True
    connected: bool = True
    transport: Literal["rest", "websocket", "demo"] = "rest"
    source: Literal["demo", "public"] = "public"

    @field_validator("exchange_at", "requested_at", "received_at", "processed_at")
    @classmethod
    def timestamp_utc(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            if value.tzinfo is None:
                raise ValueError("Timezone-aware timestamps required")
            return value.astimezone(UTC)
        return value

    @model_validator(mode="after")
    def integrity(self) -> "Book":
        for levels, reverse in ((self.asks, False), (self.bids, True)):
            if len({x.price for x in levels}) != len(levels):
                raise ValueError("Duplicate book price levels")
            if levels != sorted(levels, key=lambda x: x.price, reverse=reverse):
                raise ValueError("Book levels must be sorted best-to-worst")
        if self.bids and self.asks and self.bids[0].price > self.asks[0].price:
            raise ValueError("Crossed book")
        return self

    def age_ms(self, instant: datetime) -> int:
        times = [self.received_at]
        if self.exchange_at:
            times.append(self.exchange_at)
        if self.requested_at:
            times.append(self.requested_at)
        return int(max((instant - t).total_seconds() * 1000 for t in times))


class Match(Model):
    id: str
    event_id: str
    first_market_id: str
    second_market_id: str
    first_rules_hash: str
    second_rules_hash: str
    status: Literal["approved", "review", "rejected"]
    confidence: Price
    inverted: bool = False
    reasons: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    human_reviewed: bool = False
    version: str = "deterministic-v1"


class AdditionalCosts(Model):
    verified: bool = False
    settlement_per_contract: Nonnegative = D("0")
    rebalancing_per_contract: Nonnegative = D("0")
    fixed_per_leg: Nonnegative = D("0")
    evidence: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def evidence_required(self) -> "AdditionalCosts":
        if self.verified and not self.evidence.strip():
            raise ValueError("Additional-cost verification requires evidence")
        return self

    def total(self, quantity: Decimal) -> Decimal:
        return (
            quantity * (self.settlement_per_contract + self.rebalancing_per_contract)
            + self.fixed_per_leg
        )


class RiskSettings(Model):
    additional_costs: dict[Venue, AdditionalCosts] = Field(default_factory=dict)
    allow_usdc_parity_assumption: bool = False
    max_contracts: int = Field(default=10000, ge=1, le=100000)
    max_per_venue: Positive = D("500")
    max_total_notional: Positive = D("1000")
    bankroll: Positive = D("10000")
    max_daily_capital: Positive = D("5000")
    max_event_exposure: Positive = D("1000")
    max_league_exposure: Positive = D("3000")
    max_open_trades: int = Field(default=10, ge=1, le=1000)
    max_daily_trades: int = Field(default=5, ge=0, le=1000)
    max_daily_loss: Positive = D("100")
    max_unhedged_quantity: Nonnegative = D("0")
    max_simultaneous_executions: int = Field(default=5, ge=1, le=100)
    min_edge: Nonnegative = D("0.005")
    min_profit: Nonnegative = D("1")
    min_confidence: Price = D("0.99")
    max_quote_age_ms: int = Field(default=3000, ge=50, le=60000)
    max_book_skew_ms: int = Field(default=1500, ge=0, le=60000)
    slippage_bps: Nonnegative = D("15")
    latency_buffer_bps: Nonnegative = D("10")
    max_slippage_bps: Nonnegative = D("100")
    latency_ms: int = Field(default=400, ge=0, le=60000)
    liquidity_haircut: Price = D("0.9")
    fill_model: Literal["conservative", "optimistic", "observed"] = "conservative"
    sizing_mode: Literal[
        "per_venue", "total_notional", "fixed_quantity", "max_depth", "target_profit", "bankroll"
    ] = "per_venue"
    fixed_quantity: Positive = D("100")
    target_profit: Positive = D("10")
    bankroll_fraction: Annotated[Decimal, Field(gt=0, le=1)] = D("0.1")
    kill_switch: bool = True
    venue_kill_switches: list[Venue] = Field(default_factory=list)
    supported_leagues: list[str] = Field(
        default_factory=lambda: ["NFL", "NBA", "MLB", "NHL", "NCAAF", "NCAAB", "EPL", "ATP", "WTA"]
    )
    blocked_markets: list[str] = Field(default_factory=list)
    require_human_review: bool = False
    cooldown_seconds: int = Field(default=30, ge=0, le=3600)
    reporting_target: int = Field(default=5, ge=0, le=100)

    @model_validator(mode="after")
    def consistent(self) -> "RiskSettings":
        if self.slippage_bps > self.max_slippage_bps:
            raise ValueError("Slippage exceeds risk maximum")
        if not self.supported_leagues or len(set(self.supported_leagues)) != len(
            self.supported_leagues
        ):
            raise ValueError("Supported leagues must be nonempty and unique")
        if self.liquidity_haircut == 0:
            raise ValueError("Liquidity haircut must be positive")
        return self


class Calculation(Model):
    quantity: Positive
    cost_one: Amount
    cost_two: Amount
    price_one: Price
    price_two: Price
    limit_one: Price
    limit_two: Price
    fee_one: Amount
    fee_two: Amount
    slippage: Amount
    safety_buffer: Amount
    additional_cost_one: Amount = D("0")
    additional_cost_two: Amount = D("0")
    payout: Positive
    gross_profit: Decimal
    net_profit: Decimal
    net_return: Decimal
    unused_one: Decimal
    unused_two: Decimal
    binding_constraint: str
    consumed_one: list[Level]
    consumed_two: list[Level]
    version: str = "depth-decimal-v1"


class Opportunity(Model):
    id: str
    match_id: str
    event_id: str
    event: str
    sport: str
    league: str
    first_market_id: str
    second_market_id: str
    first_outcome: Side
    second_outcome: Side
    second_venue: Venue
    detected_at: datetime
    expires_at: datetime
    calculation: Calculation
    confidence: Price
    quote_age_ms: int
    risk_status: Literal["qualified", "rejected"]
    reasons: list[str]
    inputs: dict[str, Any]
    source: Literal["demo", "public"]


class Exposure(Model):
    committed: Amount = D("0")
    daily_capital: Amount = D("0")
    daily_loss: Amount = D("0")
    event_capital: Amount = D("0")
    league_capital: Amount = D("0")
    open_trades: int = 0
    daily_trades: int = 0
    pending_executions: int = 0
    unhedged_quantity: Nonnegative = D("0")
    cooldown_until: datetime | None = None
    settled_profit: Decimal = D("0")


class PaperState(StrEnum):
    CREATED = "CREATED"
    READY = "READY"
    SUBMITTED = "SUBMITTED"
    HEDGED = "HEDGED"
    UNHEDGED = "UNHEDGED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    SETTLED = "SETTLED"


class PaperFill(Model):
    quantity: Nonnegative = D("0")
    cost: Amount = D("0")
    fee: Amount = D("0")
    price: Price | None = None
    consumed: list[Level] = Field(default_factory=list)


class PaperTrade(Model):
    id: str
    opportunity_id: str
    event_id: str
    league: str
    state: PaperState = PaperState.CREATED
    quantity: Positive
    decision_at: datetime
    due_at: datetime
    fill_at: datetime | None = None
    first: PaperFill = Field(default_factory=PaperFill)
    second: PaperFill = Field(default_factory=PaperFill)
    unhedged_quantity: Nonnegative = D("0")
    expected_profit: Decimal
    locked_profit: Decimal | None = None
    settlement_profit: Decimal | None = None
    settlement_at: datetime | None = None
    reserved_capital: Amount
    model: Literal["conservative", "optimistic", "observed"]
    model_version: str = "paper-ioc-v1"
    failure_reason: str | None = None
    transitions: list[dict[str, str]] = Field(default_factory=list)
    settings: RiskSettings
    source: Literal["demo", "public"]
    replay_of: str | None = None


class Page(Model):
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int

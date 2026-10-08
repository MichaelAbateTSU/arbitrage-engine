"""Bounded, conditional research for common-measurement Kalshi BTC contracts."""

import re
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from itertools import combinations
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BeforeValidator, Field, model_validator

from app.domain import Book, D, FeeSpec, Level, Model, PriceRange, Side, exact_decimal, fingerprint
from app.pricing import consume, cost, fee, quantity
from app.venues.http import VenueError

TERMS_URL = "https://assets.kalshi.com/contract_terms/BTC.pdf"
TERMS_SHA256 = "7bba1b819bd027e69755c76ed082e1004eb9466e170c1ae38996cf7d52e322a6"
DEFAULT_CAPITAL = D("500")
Exact = Annotated[Decimal, BeforeValidator(exact_decimal)]
Boundary = Annotated[Exact, Field(ge=0, max_digits=24, decimal_places=8)]

RULE = re.compile(
    r"^If the simple average of the sixty seconds of CF Benchmarks' Bitcoin Real-Time "
    r"Index \(BRTI\) before (?P<before>\d{1,2} (?:AM|PM) (?:EDT|EST)) is "
    r"(?P<comparison>above|below|between) (?P<lower>\d+(?:\.\d+)?)"
    r"(?:-(?P<upper>\d+(?:\.\d+)?))? at "
    r"(?P<at>\d{1,2} (?:AM|PM) (?:EDT|EST)) on "
    r"(?P<date>[A-Za-z]+ \d{1,2}, \d{4}), then the market resolves to Yes\.$"
)


def cf_source(sources: Any) -> bool:
    return (
        isinstance(sources, list)
        and len(sources) == 1
        and isinstance(sources[0], dict)
        and sources[0].get("name") == "CF Benchmarks"
        and isinstance(sources[0].get("url"), str)
        and sources[0]["url"].split("?", 1)[0] == "https://www.cfbenchmarks.com/data/indices/BRTI"
    )


class Predicate(Model):
    kind: Literal["greater", "less", "between"]
    lower: Boundary
    upper: Boundary | None = None

    @model_validator(mode="after")
    def consistent(self) -> "Predicate":
        if self.kind == "between":
            if self.upper is None or self.lower > self.upper:
                raise ValueError("INVALID_RANGE_BOUNDARIES")
        elif self.upper is not None:
            raise ValueError("UNEXPECTED_RANGE_BOUNDARY")
        return self

    def pays_yes(self, value: Decimal) -> bool:
        if self.kind == "greater":
            return value > self.lower
        if self.kind == "less":
            return value < self.lower
        return self.upper is not None and self.lower <= value <= self.upper


class ResearchMarket(Model):
    ticker: str
    event_ticker: str
    series_ticker: Literal["KXBTCD", "KXBTC"]
    measurement_end: AwareDatetime
    predicate: Predicate
    rules_hash: str
    active: bool
    price_ranges: list[PriceRange]
    fee: FeeSpec


def parse_market(
    raw: dict[str, Any], event: dict[str, Any], series: dict[str, Any], fee_spec: FeeSpec
) -> ResearchMarket:
    if any(
        not isinstance(raw.get(key), str) or not raw[key]
        for key in ("ticker", "event_ticker", "rules_primary", "close_time")
    ) or not isinstance(event.get("strike_date"), str):
        raise VenueError("INVALID_MARKET_METADATA")
    matched = RULE.fullmatch(raw["rules_primary"])
    if (
        matched is None
        or raw.get("market_type") != "binary"
        or exact_decimal(raw.get("notional_value_dollars")) != 1
        or series.get("contract_terms_url") != TERMS_URL
        or not cf_source(series.get("settlement_sources"))
        or not cf_source(event.get("settlement_sources"))
        or event.get("series_ticker") != series.get("ticker")
        or raw.get("event_ticker") != event.get("event_ticker")
    ):
        raise VenueError("SINGLE_VENUE_PRODUCT_UNSUPPORTED")
    values = matched.groupdict()
    if values["before"] != values["at"]:
        raise VenueError("SINGLE_VENUE_MEASUREMENT_MISMATCH")
    clock, zone = values["at"].rsplit(" ", 1)
    offset = timezone(timedelta(hours=-4 if zone == "EDT" else -5))
    end = datetime.strptime(f"{values['date']} {clock}", "%b %d, %Y %I %p")
    end = end.replace(tzinfo=offset).astimezone(UTC)
    close = datetime.fromisoformat(raw["close_time"].replace("Z", "+00:00"))
    strike_date = datetime.fromisoformat(event["strike_date"].replace("Z", "+00:00"))
    if close.tzinfo is None or strike_date.tzinfo is None or close != end or strike_date != end:
        raise VenueError("SINGLE_VENUE_MEASUREMENT_MISMATCH")
    kinds: dict[str, Literal["greater", "less", "between"]] = {
        "above": "greater",
        "below": "less",
        "between": "between",
    }
    kind = kinds[values["comparison"]]
    lower = exact_decimal(values["lower"])
    upper = exact_decimal(values["upper"]) if values["upper"] else None
    field = "cap_strike" if kind == "less" else "floor_strike"
    if raw.get("strike_type") != kind or exact_decimal(raw.get(field)) != lower:
        raise VenueError("SINGLE_VENUE_PREDICATE_MISMATCH")
    if kind == "between" and exact_decimal(raw.get("cap_strike")) != upper:
        raise VenueError("SINGLE_VENUE_PREDICATE_MISMATCH")
    identity = {
        key: raw.get(key)
        for key in (
            "ticker",
            "event_ticker",
            "rules_primary",
            "rules_secondary",
            "close_time",
            "floor_strike",
            "cap_strike",
            "strike_type",
            "price_ranges",
        )
    }
    identity["terms_sha256"] = TERMS_SHA256
    identity["event_settlement_sources"] = event["settlement_sources"]
    identity["series_settlement_sources"] = series["settlement_sources"]
    return ResearchMarket(
        ticker=raw["ticker"],
        event_ticker=raw["event_ticker"],
        series_ticker=event["series_ticker"],
        measurement_end=end,
        predicate=Predicate(kind=kind, lower=lower, upper=upper),
        rules_hash=fingerprint(identity),
        active=raw.get("status") == "active",
        price_ranges=[PriceRange.model_validate(value) for value in raw["price_ranges"]],
        fee=fee_spec,
    )


def scenario_points(predicates: list[Predicate]) -> list[Decimal]:
    boundaries = sorted(
        {
            D("0"),
            *(value.lower for value in predicates),
            *(value.upper for value in predicates if value.upper is not None),
        }
    )
    return sorted(
        {
            *boundaries,
            boundaries[-1] + 1,
            *(
                (first + second) / 2
                for first, second in zip(boundaries, boundaries[1:], strict=False)
            ),
        }
    )


def payout_floor(
    predicates: list[Predicate], outcomes: list[Side]
) -> tuple[Decimal, Decimal, Decimal]:
    if not predicates or len(predicates) != len(outcomes):
        raise ValueError("INVALID_PAYOUT_LEGS")
    normal = [
        sum(
            (
                D(
                    predicate.pays_yes(value)
                    if outcome == Side.YES
                    else not predicate.pays_yes(value)
                )
                for predicate, outcome in zip(predicates, outcomes, strict=True)
            ),
            D("0"),
        )
        for value in scenario_points(predicates)
    ]
    missing = D(sum(outcome == Side.NO for outcome in outcomes))
    return min(normal), max(normal), missing


def relationships(
    markets: list[ResearchMarket],
) -> list[tuple[ResearchMarket, Side, ResearchMarket, Side]]:
    result = []
    for first, second in combinations(markets, 2):
        if (
            first.ticker == second.ticker
            or first.event_ticker != second.event_ticker
            or first.measurement_end != second.measurement_end
            or first.series_ticker != second.series_ticker
        ):
            continue
        a, b = first.predicate, second.predicate
        if first.series_ticker == "KXBTCD" and a.kind == b.kind == "greater":
            lower, higher = (first, second) if a.lower < b.lower else (second, first)
            if a.lower != b.lower:
                result.append((lower, Side.YES, higher, Side.NO))
        elif first.series_ticker == "KXBTC":
            floor, _, missing = payout_floor([a, b], [Side.NO, Side.NO])
            if floor >= 1 and missing >= 1:
                result.append((first, Side.NO, second, Side.NO))
    return result


def quote_pair(
    first: ResearchMarket,
    outcome_one: Side,
    second: ResearchMarket,
    outcome_two: Side,
    books: dict[tuple[str, Side], Book],
    at: datetime,
    max_quantity: int = 100,
    max_capital: Decimal = DEFAULT_CAPITAL,
) -> dict[str, Any]:
    return quote_bundle(
        [first, second], [outcome_one, outcome_two], books, at, max_quantity, max_capital
    )


def quote_bundle(
    markets: list[ResearchMarket],
    outcomes: list[Side],
    books: dict[tuple[str, Side], Book],
    at: datetime,
    max_quantity: int = 100,
    max_capital: Decimal = DEFAULT_CAPITAL,
) -> dict[str, Any]:
    if not 1 <= max_quantity <= 1000 or not max_capital.is_finite() or max_capital <= 0:
        raise ValueError("INVALID_SCREEN_LIMITS")
    if (
        at.tzinfo is None
        or not 2 <= len(markets) <= 20
        or len(markets) != len(outcomes)
        or len({market.ticker for market in markets}) != len(markets)
        or len({market.event_ticker for market in markets}) != 1
        or len({market.measurement_end for market in markets}) != 1
    ):
        raise ValueError("DIFFERENT_MEASUREMENT_GROUPS")
    first = markets[0]
    floor, maximum, missing = payout_floor([market.predicate for market in markets], outcomes)
    normal_floor = min(floor, missing)
    identifier = fingerprint(
        [
            first.event_ticker,
            sorted(
                [market.ticker, market.rules_hash, str(outcome)]
                for market, outcome in zip(markets, outcomes, strict=True)
            ),
        ]
    )
    result: dict[str, Any] = {
        "id": identifier,
        "event_ticker": first.event_ticker,
        "legs": [
            [market.ticker, outcome] for market, outcome in zip(markets, outcomes, strict=True)
        ],
        "normal_floor": str(floor),
        "normal_maximum": str(maximum),
        "no_data_floor": str(missing),
        "minimum_all_scenario_payout": None,
        "exception_floor": None,
        "family_classification": "potentially_compatible"
        if normal_floor >= 1
        else "proven_incompatible",
        "reasons": [
            "OUTCOME_REVIEW_JOINT_PAYOUT_FLOOR_UNVERIFIED",
            "ACCOUNT_ORDER_PERMISSION_UNVERIFIED",
            "ALL_IN_ADDITIONAL_COSTS_UNVERIFIED",
        ],
        "best_conditional_size": None,
        "best_conditional_net_before_additional_costs": None,
        "economic_positive_before_additional_costs": False,
        "threshold_positive_before_additional_costs": False,
        "eligible": False,
        "shadow_qualified": False,
        "orders_submitted": False,
    }
    if normal_floor < 1:
        result["reasons"].append("KNOWN_SETTLEMENT_SCENARIO_BREAKS_HEDGE")
        return result
    if any(not market.active for market in markets) or at >= first.measurement_end:
        result["reasons"].append("EVENT_NOT_OPEN")
        return result
    requested = [
        books.get((market.ticker, outcome))
        for market, outcome in zip(markets, outcomes, strict=True)
    ]
    if any(book is None for book in requested):
        result["reasons"].append("BOOK_MISSING")
        return result
    observed = [book for book in requested if book is not None]
    pricing_reasons: list[str] = []
    for market, outcome, book in zip(markets, outcomes, observed, strict=True):
        if book.market_id != market.ticker or book.outcome != outcome or book.source != "public":
            pricing_reasons.append("BOOK_IDENTITY_MISMATCH")
        if not book.connected or not book.synchronized:
            pricing_reasons.append("BOOK_INVALID")
        if book.age_ms(at) > 5000:
            pricing_reasons.append("BOOK_STALE")
        if any(
            value is not None and value > at + timedelta(seconds=1)
            for value in (book.requested_at, book.received_at, book.exchange_at)
        ):
            pricing_reasons.append("CLOCK_DRIFT")
        if not market.price_ranges or any(
            not any(
                grid.accepts(level.price if outcome == Side.YES else 1 - level.price)
                for grid in market.price_ranges
            )
            for level in book.asks + book.bids
        ):
            pricing_reasons.append("INVALID_PRICE_GRID")
        if not market.fee.known_at(at):
            pricing_reasons.append("FEE_UNVERIFIED_OR_EXPIRED")
    if (
        max(book.received_at for book in observed) - min(book.received_at for book in observed)
    ).total_seconds() > 1:
        pricing_reasons.append("BOOK_TIME_SKEW")
    result["reasons"].extend(sorted(set(pricing_reasons)))
    if pricing_reasons:
        return result
    if any(not book.asks for book in observed):
        result["reasons"].append("EMPTY_PURCHASE_DEPTH")
        return result
    best: Decimal | None = None
    sizes = (
        1
        if sum((book.asks[0].price for book in observed), D("0")) >= normal_floor
        else max_quantity
    )
    for size in range(1, sizes + 1):
        q = D(size)
        consumed = [consume(book.asks, q) for book in observed]
        if any(quantity(levels) != q for levels in consumed):
            break
        acquisition = sum((cost(levels) for levels in consumed), D("0"))
        fees = sum(
            (fee(levels, market.fee) for levels, market in zip(consumed, markets, strict=True)),
            D("0"),
        )
        buffers = acquisition * D("0.0025")
        if acquisition + fees + buffers > max_capital:
            break
        net = q * normal_floor - acquisition - fees - buffers
        if best is None or net > best:
            best = net
            result.update(
                best_conditional_size=size,
                best_conditional_net_before_additional_costs=str(net),
                acquisition_cost=str(acquisition),
                trading_fee_bound=str(fees),
                execution_buffer=str(buffers),
                gross_before_fees=str(q * normal_floor - acquisition),
                required_full_purchase_capital=str(acquisition + fees + buffers),
            )
    if best is None:
        result["reasons"].append("INSUFFICIENT_DEPTH_OR_CAPITAL")
    else:
        result["economic_positive_before_additional_costs"] = best > 0
        result["threshold_positive_before_additional_costs"] = best >= 1 and best >= exact_decimal(
            result["acquisition_cost"]
        ) * D("0.005")
        if best <= 0:
            result["reasons"].append("NONPOSITIVE_AFTER_FEES_AND_BUFFERS")
    return result


class EpisodeTracker:
    def __init__(self) -> None:
        self.active: dict[str, dict[str, Any]] = {}
        self.closed: list[dict[str, Any]] = []
        self.pair_ids: dict[str, str] = {}

    def interrupt(self, event_ticker: str) -> None:
        for value in self.active.values():
            if value["event_ticker"] == event_ticker:
                value["coverage_interrupted"] = True

    def observe(self, values: list[dict[str, Any]], at: datetime) -> None:
        for value in values:
            identifier = value["id"]
            pair_key = fingerprint([value["event_ticker"], sorted(value["legs"])])
            previous = self.pair_ids.get(pair_key)
            if previous is not None and previous != identifier:
                self.closed.append(
                    {
                        **self.active.pop(previous),
                        "ended_at": at.isoformat(),
                        "end_reason": "SPECIFICATION_CHANGED",
                    }
                )
                self.pair_ids.pop(pair_key)
            positive = value["economic_positive_before_additional_costs"]
            if value["best_conditional_size"] is None:
                if identifier in self.active:
                    self.active[identifier]["coverage_interrupted"] = True
                continue
            if positive and identifier not in self.active:
                self.active[identifier] = {
                    "id": identifier,
                    "event_ticker": value["event_ticker"],
                    "started_at": at.isoformat(),
                    "observations": 0,
                    "qualified": False,
                    "basis": "conditional_economics_before_unknown_costs_not_validated_arbitrage",
                }
                self.pair_ids[pair_key] = identifier
            if positive:
                self.active[identifier]["observations"] += 1
                self.active[identifier]["last_observed_at"] = at.isoformat()
            elif identifier in self.active:
                self.closed.append({**self.active.pop(identifier), "ended_at": at.isoformat()})
                self.pair_ids.pop(pair_key, None)


def execution_stress(
    first: Book,
    second: Book,
    unwind: Book,
    size: Decimal,
    spec: FeeSpec,
    scenario: Literal["delayed", "partial_second", "rejected_second"],
) -> dict[str, Any]:
    """Synthetic cashflow exercise only; not a persisted or settlement-qualified paper trade."""
    size = exact_decimal(size)
    if size <= 0 or scenario not in ("delayed", "partial_second", "rejected_second"):
        raise ValueError("INVALID_STRESS_INPUT")
    one = consume(first.asks, size)
    two = (
        []
        if scenario == "rejected_second"
        else consume(second.asks, size / 2 if scenario == "partial_second" else size)
    )
    matched = min(quantity(one), quantity(two))
    leftover = abs(quantity(one) - quantity(two))
    if quantity(two) > quantity(one):
        raise ValueError("STRESS_REQUIRES_FIRST_LEG_EXCESS")
    # consume()'s buy-price cap must not truncate a descending sell ladder.
    sold: list[Level] = consume(unwind.bids, leftover) if leftover else []
    remaining = leftover - quantity(sold)
    spent = cost(one) + cost(two) + fee(one, spec) + fee(two, spec)
    proceeds = cost(sold) - fee(sold, spec)
    return {
        "scenario": scenario,
        "source": "synthetic_stress",
        "matched_quantity": str(matched),
        "unresolved_quantity": str(remaining),
        "conditional_pnl_at_unit_floor": str(matched + proceeds - spent)
        if remaining == 0
        else None,
        "realized_unwind_cashflow": str(proceeds - spent)
        if matched == 0 and remaining == 0
        else None,
        "payout_floor_is_verified": False,
        "qualified": False,
        "orders_submitted": False,
    }

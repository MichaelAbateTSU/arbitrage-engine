import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from app.domain import D, FeeSpec, Market, PriceRange, Side, Venue, now
from app.matching import LEAGUES, Alias, extract_rules, normalize_team
from app.venues.http import decode
from app.venues.schemas import GammaMarket, KalshiMarket, USMarket


def utc(value: str | None) -> datetime | None:
    if not value:
        return None
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Missing timezone")
    return result.astimezone(UTC)


def league_from(value: str) -> str:
    text = value.upper()
    for league in LEAGUES:
        if re.search(rf"(?:^|[^A-Z]){league}(?:[^A-Z]|$)", text):
            return league
    return "UNKNOWN"


def price_grid(step: Decimal | None) -> list[PriceRange]:
    return [PriceRange(start=D("0"), end=D("1"), step=step)] if step else []


def public_fee(
    kind: Literal["kalshi_bound", "us_quadratic", "international", "zero", "unknown"],
    rate: Decimal,
    source: str,
) -> FeeSpec:
    return FeeSpec(
        kind=kind,
        rate=rate,
        version=f"{kind}-2026-09-v1",
        source=source,
        valid_until=now() + timedelta(minutes=15),
    )


def kalshi_market(
    wire: KalshiMarket,
    league: str,
    series: dict[str, Any],
    fee_rate: Decimal | None,
    aliases: list[Alias],
) -> Market:
    raw = wire.model_dump(mode="json")
    rules_text = f"{wire.rules_primary}\n{wire.rules_secondary}".strip()
    game = re.search(
        r"wins the (.+?) (?:Pro Football|Pro Basketball|Pro Baseball|Pro Hockey|"
        r"College Football|College Basketball|game) (?:game )?originally scheduled",
        rules_text,
        re.IGNORECASE,
    )
    participants: list[str] = []
    if game:
        participants = [
            normalize_team(x, league, aliases)
            for x in re.split(r"\s+vs\.?\s+", game.group(1), flags=re.IGNORECASE)
        ]
    yes = normalize_team(wire.yes_sub_title, league, aliases) if wire.yes_sub_title else None
    sources = series.get("settlement_sources") or []
    source = ",".join(sorted(x.get("name", "") for x in sources)) or None
    fee = (
        public_fee("kalshi_bound", fee_rate, "series and effective event fee override")
        if fee_rate is not None
        else FeeSpec()
    )
    return Market(
        id=f"kalshi:{wire.ticker}",
        venue=Venue.KALSHI,
        external_id=wire.ticker,
        event_id=wire.event_ticker,
        title=wire.title,
        rules_text=rules_text,
        participants=participants,
        yes_team=yes,
        league=league,
        sport=LEAGUES.get(league, "UNKNOWN"),
        # occurrence_datetime can be expected completion, NOT game start.
        start_time=None,
        market_type="moneyline" if wire.market_type == "binary" else "unsupported",
        status="open" if wire.status == "active" else wire.status,
        tradable=wire.status == "active",
        price_ranges=[PriceRange.model_validate(x) for x in wire.price_ranges],
        rules=extract_rules(rules_text, source),
        fee=fee,
        raw=raw,
        result=D("1") if wire.result == "yes" else (D("0") if wire.result == "no" else None),
    )


def gamma_market(wire: GammaMarket, aliases: list[Alias]) -> Market:
    raw = wire.model_dump(mode="json")
    events = wire.events
    event = events[0] if events else {}
    league = league_from(wire.slug + " " + str(event.get("slug", "")))
    labels = decode(wire.outcomes)
    tokens = decode(wire.clobTokenIds or "[]")
    if not isinstance(labels, list) or not isinstance(tokens, list):
        raise ValueError("INVALID_TOKEN_OUTCOME_ARRAY")
    title = str(event.get("title", wire.question or wire.slug))
    participants = [
        normalize_team(x, league, aliases)
        for x in re.split(r"\s+(?:vs\.?|at|@)\s+", title, flags=re.IGNORECASE)
    ]
    if len(participants) != 2:
        participants = []
    yes: str | None = None
    if len(labels) == 2:
        if normalized_label := str(labels[0]):
            if normalized_label.lower() not in ("yes", "no"):
                yes = normalize_team(normalized_label, league, aliases)
            elif wire.question:
                for team in participants:
                    if team in wire.question.lower():
                        yes = team
    fee = FeeSpec()
    if wire.feesEnabled is False:
        fee = public_fee("zero", D("0"), "Gamma feesEnabled=false")
    elif wire.feesEnabled is True and wire.feeSchedule:
        if wire.feeSchedule.get("exponent") == 1 and wire.feeSchedule.get("takerOnly") is True:
            rate = D(str(wire.feeSchedule["rate"]))
            fee = public_fee("international", rate, "Gamma feeSchedule")
    return Market(
        id=f"polymarket_international:{wire.id}",
        venue=Venue.INTERNATIONAL,
        quote_currency="USDC",
        external_id=wire.slug,
        event_id=str(event.get("id", wire.id)),
        title=wire.question or wire.slug,
        rules_text=wire.description,
        league=league,
        sport=LEAGUES.get(league, "UNKNOWN"),
        participants=participants,
        yes_team=yes,
        start_time=utc(wire.gameStartTime),
        start_time_verified=wire.gameStartTime is not None,
        market_type="moneyline" if wire.sportsMarketType == "moneyline" else "unsupported",
        status="closed" if wire.closed else ("open" if wire.active else "inactive"),
        tradable=wire.active and not wire.closed and wire.acceptingOrders and wire.enableOrderBook,
        token_ids={Side.YES: str(tokens[0]), Side.NO: str(tokens[1])} if len(tokens) == 2 else {},
        price_ranges=price_grid(wire.orderPriceMinTickSize),
        minimum_notional=wire.orderMinSize or D("0"),
        quantity_step=D("1"),
        rules=extract_rules(wire.description, wire.resolutionSource),
        fee=fee,
        raw=raw,
    )


def us_market(wire: USMarket, aliases: list[Alias]) -> Market:
    league = "UNKNOWN"
    participants = []
    yes: str | None = None
    for side in wire.marketSides:
        team = side.get("team") or {}
        side_league = str(team.get("league", "")).upper()
        if side_league:
            league = side_league
        name = str(team.get("name", side.get("description", "")))
        if name:
            canonical = normalize_team(name, league, aliases)
            participants.append(canonical)
            if side.get("long") is True:
                yes = canonical
    fee = (
        public_fee("us_quadratic", wire.feeCoefficient, "market feeCoefficient")
        if wire.feeCoefficient is not None
        else FeeSpec()
    )
    return Market(
        id=f"polymarket_us:{wire.id}",
        venue=Venue.US,
        external_id=wire.slug,
        event_id=wire.slug,
        title=wire.question or wire.slug,
        rules_text=wire.description,
        league=league,
        sport=LEAGUES.get(league, "UNKNOWN"),
        participants=participants,
        yes_team=yes,
        start_time=utc(wire.gameStartTime),
        start_time_verified=wire.gameStartTime is not None,
        market_type="moneyline"
        if (
            wire.sportsMarketType in ("moneyline", "SPORTS_MARKET_TYPE_MONEYLINE")
            or wire.sportsMarketTypeV2 == "SPORTS_MARKET_TYPE_MONEYLINE"
        )
        else "unsupported",
        status="open"
        if wire.status == "MARKET_STATUS_OPEN"
        else ("settled" if wire.status == "MARKET_STATUS_RESOLVED" else "closed"),
        tradable=wire.active and not wire.closed and wire.status == "MARKET_STATUS_OPEN",
        price_ranges=price_grid(wire.orderPriceMinTickSize),
        minimum_quantity=wire.minimumTradeQty if wire.minimumTradeQty is not None else D("1"),
        rules=extract_rules(wire.description),
        fee=fee,
        raw=wire.model_dump(mode="json"),
    )

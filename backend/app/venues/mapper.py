import re
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

from app.domain import (
    BitcoinPolicy,
    BitcoinWindow,
    D,
    FeeSpec,
    Market,
    PriceRange,
    Rules,
    Side,
    Venue,
    now,
)
from app.matching import LEAGUES, Alias, extract_rules, normalize_team
from app.venues.http import VenueError, decode
from app.venues.schemas import GammaMarket, KalshiMarket, USMarket

KALSHI_BTC_TERMS = "https://assets.kalshi.com/contract_terms/CRYPTO.pdf"
US_BTC_FAQ = "https://docs.polymarket.us/faqs/crypto-faqs"


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
    if league == "BTC":
        return kalshi_bitcoin(wire, series, fee_rate)
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
    if wire.assetPriceTerms is not None:
        return us_bitcoin(wire)
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


def kalshi_bitcoin(wire: KalshiMarket, series: dict[str, Any], fee_rate: Decimal | None) -> Market:
    text = f"{wire.rules_primary}\n{wire.rules_secondary}".strip()
    if (
        series.get("ticker") != "KXBTC15M"
        or wire.market_type != "binary"
        or wire.strike_type != "greater_or_equal"
        or wire.notional_value_dollars != 1
        or "BRTI" not in text
        or "at least" not in text.lower()
        or "simple average" not in text.lower()
        or "sixty seconds" not in text.lower()
    ):
        raise VenueError("BTC_RULES_OR_PRODUCT_UNSUPPORTED")
    start, end = kalshi_bitcoin_window(wire.rules_primary)
    if utc(wire.close_time) != end:
        raise VenueError("BTC_RULE_WINDOW_METADATA_MISMATCH")
    policy = BitcoinPolicy(
        missing_data="no",
        discretionary_settlement="independent",
        evidence={
            "missing_data": f"{KALSHI_BTC_TERMS}: incomplete data resolves affected strikes No.",
            "discretionary_settlement": f"{KALSHI_BTC_TERMS}: independent review/payout powers.",
        },
    )
    window = BitcoinWindow(
        window_start=start, window_end=end, opening_reference=wire.floor_strike, policy=policy
    )
    raw = wire.model_dump(mode="json")
    raw["bitcoin_source_policy"] = policy.model_dump(mode="json")
    raw["bitcoin_governing_terms"] = {
        key: series.get(key)
        for key in ("contract_terms_url", "last_updated_ts", "settlement_sources")
    }
    return Market(
        id=f"kalshi:{wire.ticker}",
        venue=Venue.KALSHI,
        external_id=wire.ticker,
        event_id=wire.event_ticker,
        title=wire.title,
        rules_text=text,
        league="BTC",
        sport="crypto",
        participants=["up", "down"],
        yes_team="up",
        start_time=start,
        start_time_verified=True,
        market_type="btc_up_down_15m",
        status="open" if wire.status == "active" and now() < end else "closed",
        tradable=wire.status == "active" and start <= now() < end,
        price_ranges=[PriceRange.model_validate(x) for x in wire.price_ranges],
        quantity_step=D("0.01"),
        minimum_quantity=D("0.01"),
        rules=Rules(period="15m", settlement_source="BRTI"),
        fee=public_fee("kalshi_bound", fee_rate, "KXBTC15M series and effective event fee override")
        if fee_rate is not None
        else FeeSpec(),
        bitcoin=window,
        raw=raw,
        result=D("1") if wire.result == "yes" else D("0") if wire.result == "no" else None,
    )


def us_bitcoin(wire: USMarket) -> Market:
    terms = wire.assetPriceTerms
    if terms is None:
        raise VenueError("BTC_WINDOW_IDENTITY_MISSING")
    supported = (
        terms.marketType == "ASSET_PRICE_MARKET_TYPE_UP_DOWN"
        and terms.asset.assetClass == "ASSET_CLASS_CRYPTO"
        and terms.asset.symbol.lower() == "btc"
        and terms.indexSymbol == "BRTI"
        and terms.horizon == "15m"
    )
    if not supported:
        # Other typed crypto products remain explicitly unsupported, not moneyline markets.
        raw = wire.model_dump(mode="json")
        raw["assetPriceTerms"] = None
        result = us_market(USMarket.model_validate(raw), [])
        result.market_type = "unsupported"
        result.tradable = False
        result.raw = wire.model_dump(mode="json")
        return result
    start, end = utc(terms.windowStart), utc(terms.windowEnd)
    if start is None or end is None:
        raise VenueError("BTC_WINDOW_IDENTITY_MISSING")
    if terms.priceToBeat is not None and terms.priceToBeat.currency != "USD":
        raise VenueError("BTC_REFERENCE_CURRENCY_UNSUPPORTED")
    if wire.minimumTradeQty is None or wire.minimumTradeQty <= 0:
        raise VenueError("BTC_QUANTITY_INCREMENT_UNAVAILABLE")
    if sorted(side.get("long") is True for side in wire.marketSides) != [False, True]:
        raise VenueError("BTC_OUTCOME_MAPPING_MISMATCH")
    if any(side.get("identifier") != wire.slug for side in wire.marketSides):
        raise VenueError("BTC_OUTCOME_MAPPING_MISMATCH")
    if "BRTI" not in wire.description or "greater than or equal" not in wire.description.lower():
        raise VenueError("BTC_RULES_OR_PRODUCT_UNSUPPORTED")
    policy = BitcoinPolicy(
        sample_start_offset=-59,
        sample_end_offset=0,
        missing_data="deferred_review",
        evidence={
            "sample_start_offset": f"{US_BTC_FAQ}: inclusive [T-59s,T].",
            "sample_end_offset": f"{US_BTC_FAQ}: inclusive [T-59s,T].",
            "missing_data": f"{US_BTC_FAQ}: complete data or review, never partial settlement.",
        },
    )
    window = BitcoinWindow(
        window_start=start,
        window_end=end,
        opening_reference=terms.priceToBeat.value if terms.priceToBeat else None,
        policy=policy,
    )
    raw = wire.model_dump(mode="json")
    raw["bitcoin_source_policy"] = policy.model_dump(mode="json")
    return Market(
        id=f"polymarket_us:{wire.id}",
        venue=Venue.US,
        external_id=wire.slug,
        event_id=wire.slug,
        title=wire.question or wire.slug,
        rules_text=wire.description,
        league="BTC",
        sport="crypto",
        participants=["up", "down"],
        yes_team="up",
        start_time=start,
        start_time_verified=True,
        market_type="btc_up_down_15m",
        status="settled"
        if wire.status == "MARKET_STATUS_RESOLVED"
        else "open"
        if wire.status == "MARKET_STATUS_OPEN" and now() < end
        else "closed",
        tradable=wire.active
        and not wire.closed
        and wire.status == "MARKET_STATUS_OPEN"
        and start <= now() < end,
        price_ranges=price_grid(wire.orderPriceMinTickSize),
        quantity_step=wire.minimumTradeQty,
        minimum_quantity=wire.minimumTradeQty,
        rules=Rules(period="15m", settlement_source="BRTI"),
        fee=public_fee("us_quadratic", wire.feeCoefficient, "market feeCoefficient")
        if wire.feeCoefficient is not None
        else FeeSpec(),
        bitcoin=window,
        raw=raw,
    )


def kalshi_bitcoin_window(text: str) -> tuple[datetime, datetime]:
    """Trading open_time is not assumed to be the measurement start."""
    stamps = re.findall(
        r"before (\d{1,2}):(\d{2})\s*(AM|PM)\s+(EST|EDT) on "
        r"([A-Za-z]+) (\d{1,2}), (\d{4})",
        text,
        re.IGNORECASE,
    )
    if len(stamps) != 2:
        raise VenueError("BTC_RULE_WINDOW_UNPARSEABLE")
    months = {
        name: index
        for index, names in enumerate(
            (
                "jan january",
                "feb february",
                "mar march",
                "apr april",
                "may",
                "jun june",
                "jul july",
                "aug august",
                "sep sept september",
                "oct october",
                "nov november",
                "dec december",
            ),
            1,
        )
        for name in names.split()
    }
    values = []
    for hour, minute, meridian, zone, month, day, year in stamps:
        if month.lower() not in months or not 1 <= int(hour) <= 12:
            raise VenueError("BTC_RULE_WINDOW_UNPARSEABLE")
        clock_hour = int(hour) % 12 + (12 if meridian.upper() == "PM" else 0)
        value = datetime(
            int(year),
            months[month.lower()],
            int(day),
            clock_hour,
            int(minute),
            tzinfo=timezone(timedelta(hours=-4 if zone.upper() == "EDT" else -5)),
        )
        values.append(value.astimezone(UTC))
    end, start = values
    return start, end

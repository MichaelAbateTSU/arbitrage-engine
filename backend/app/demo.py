from datetime import UTC, datetime, timedelta

from app.domain import (
    Book,
    D,
    FeeSpec,
    Level,
    Market,
    PriceRange,
    Rules,
    Side,
    Venue,
    now,
)
from app.matching import LEAGUES
from app.pricing import ordered

DEMO_EVENTS = [
    ("NFL", "Atlanta Falcons", "Carolina Panthers", Venue.US, "valid"),
    ("NBA", "Boston Celtics", "New York Knicks", Venue.INTERNATIONAL, "valid"),
    ("MLB", "New York Yankees", "Boston Red Sox", Venue.US, "valid"),
    ("NHL", "Toronto Maple Leafs", "Boston Bruins", Venue.INTERNATIONAL, "valid"),
    ("NFL", "Buffalo Bills", "Los Angeles Rams", Venue.US, "mismatch"),
    ("EPL", "Arsenal", "Chelsea", Venue.INTERNATIONAL, "review"),
]
DEMO_RULES = (
    "Full game winner including overtime. No draw is possible. "
    "If cancelled, both outcomes refund 50-50. Postponed games remain open until completed. "
    "Settlement source: official league final result."
)


def demo_markets() -> list[Market]:
    date = now().date()
    start = datetime(date.year, date.month, date.day, 20, tzinfo=UTC) + timedelta(days=1)
    result = []
    for index, (league, home, away, other, scenario) in enumerate(DEMO_EVENTS):
        participants = [home.lower(), away.lower()]
        rules = Rules(
            period="full_game",
            overtime="included",
            draw="impossible",
            cancellation="half_refund",
            postponement="until_completed",
            settlement_source="official league final result",
            evidence={
                field: DEMO_RULES
                for field in (
                    "period",
                    "overtime",
                    "draw",
                    "cancellation",
                    "postponement",
                    "settlement_source",
                )
            },
        )
        for venue in (Venue.KALSHI, other):
            variant = rules.model_copy(deep=True)
            if scenario == "mismatch" and venue != Venue.KALSHI:
                variant.overtime = "excluded"
            if scenario == "review" and venue != Venue.KALSHI:
                variant.settlement_source = None
                variant.evidence.pop("settlement_source")
            identifier = f"demo-{date.isoformat()}-{index}"
            fee = FeeSpec(
                kind="kalshi_bound"
                if venue == Venue.KALSHI
                else ("us_quadratic" if venue == Venue.US else "international"),
                rate=D("0.07")
                if venue == Venue.KALSHI
                else (D("0.0695") if venue == Venue.US else D("0.05")),
                version="synthetic-demo-v1",
                source="synthetic fixture",
                valid_until=now() + timedelta(hours=1),
            )
            result.append(
                Market(
                    id=f"{venue}:{identifier}",
                    venue=venue,
                    external_id=identifier,
                    event_id=f"demo-event-{index}-{date}",
                    title=f"{home} vs {away}: {home} wins",
                    rules_text=DEMO_RULES,
                    league=league,
                    sport=LEAGUES[league],
                    participants=participants,
                    yes_team=participants[0],
                    start_time=start,
                    start_time_verified=True,
                    market_type="moneyline",
                    status="open",
                    tradable=True,
                    price_ranges=[PriceRange(start=D("0"), end=D("1"), step=D("0.01"))],
                    rules=variant,
                    fee=fee,
                    source="demo",
                    raw={"scenario": scenario, "label": "SYNTHETIC DEMO - NOT LIVE MARKET DATA"},
                )
            )
    return result


def demo_books(markets: list[Market], tick: int) -> list[Book]:
    books = []
    at = now()
    for market in markets:
        scenario = market.raw["scenario"]
        for side in Side:
            if market.venue == Venue.KALSHI:
                base = D("0.40") if side == Side.YES else D("0.62")
            else:
                base = D("0.49") if side == Side.NO else D("0.54")
            # Repeatable adverse movement/one-sided depth disappearance.
            phase = tick % 16
            if (
                market.league == "MLB"
                and market.venue != Venue.KALSHI
                and side == Side.NO
                and phase in (3, 4, 5)
            ):
                base = D("0.64")
            size = D("600")
            if market.league == "MLB" and market.venue != Venue.KALSHI and phase in (7, 8):
                size = D("20")
            if scenario == "review":
                base += D("0.02")
            books.append(
                Book(
                    market_id=market.id,
                    outcome=side,
                    bids=[Level(price=base - D("0.02"), quantity=D("450"))],
                    asks=ordered(
                        [
                            Level(price=base, quantity=size),
                            Level(price=base + D("0.01"), quantity=D("500")),
                            Level(price=base + D("0.08"), quantity=D("1200")),
                        ]
                    ),
                    sequence=tick,
                    exchange_at=at,
                    received_at=at,
                    processed_at=at,
                    transport="demo",
                    source="demo",
                )
            )
    return books

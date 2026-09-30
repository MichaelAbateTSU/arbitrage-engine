from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.db import AggregateRow, HealthRow, OpportunityRow, RejectionRow, WorkerRow
from app.domain import D, PaperState, now
from app.store import Store, exposure_for


@dataclass(frozen=True)
class ObservationSummary:
    match_id: str
    first_market_id: str
    second_market_id: str
    league: str
    first_outcome: str
    second_outcome: str
    second_venue: str
    detected_at: datetime
    expires_at: datetime
    risk_status: str
    quote_age_ms: int
    quantity: D
    net_profit: D
    net_return: D


async def summary(store: Store, day: str | None = None) -> dict[str, Any]:
    selected = day or now().date().isoformat()
    start = datetime.fromisoformat(selected).replace(tzinfo=UTC)
    end = start + timedelta(days=1)
    trades = await store.paper_trades()
    daily = [x for x in trades if x.decision_at.date().isoformat() == selected]
    async with store.sessions() as session:
        rows = (
            await session.execute(
                select(
                    OpportunityRow.match_id,
                    OpportunityRow.league,
                    OpportunityRow.status,
                    OpportunityRow.payload["first_market_id"].as_string().label("first_market_id"),
                    OpportunityRow.payload["second_market_id"]
                    .as_string()
                    .label("second_market_id"),
                    OpportunityRow.payload["first_outcome"].as_string().label("first_outcome"),
                    OpportunityRow.payload["second_outcome"].as_string().label("second_outcome"),
                    OpportunityRow.payload["second_venue"].as_string().label("second_venue"),
                    OpportunityRow.payload["detected_at"].as_string().label("detected_at"),
                    OpportunityRow.payload["expires_at"].as_string().label("expires_at"),
                    OpportunityRow.payload["quote_age_ms"].as_integer().label("quote_age_ms"),
                    OpportunityRow.payload["calculation"]["quantity"].as_string().label("quantity"),
                    OpportunityRow.payload["calculation"]["net_profit"]
                    .as_string()
                    .label("net_profit"),
                    OpportunityRow.payload["calculation"]["net_return"]
                    .as_string()
                    .label("net_return"),
                ).where(
                    OpportunityRow.source == store.source,
                    OpportunityRow.created_at >= start,
                    OpportunityRow.created_at < end,
                )
            )
        ).all()
        rejection_rows = (
            await session.scalars(
                select(RejectionRow.reason).where(
                    RejectionRow.source == store.source,
                    RejectionRow.created_at >= start,
                    RejectionRow.created_at < end,
                )
            )
        ).all()
        health = [
            x.payload
            for x in (
                await session.scalars(select(HealthRow).where(HealthRow.source == store.source))
            ).all()
        ]
        workers = [
            x.payload
            for x in (
                await session.scalars(select(WorkerRow).where(WorkerRow.source == store.source))
            ).all()
        ]
    opportunities = [
        ObservationSummary(
            match_id=row.match_id,
            league=row.league,
            risk_status=row.status,
            first_market_id=row.first_market_id,
            second_market_id=row.second_market_id,
            first_outcome=row.first_outcome,
            second_outcome=row.second_outcome,
            second_venue=row.second_venue,
            quote_age_ms=row.quote_age_ms,
            detected_at=datetime.fromisoformat(row.detected_at.replace("Z", "+00:00")),
            expires_at=datetime.fromisoformat(row.expires_at.replace("Z", "+00:00")),
            quantity=D(row.quantity),
            net_profit=D(row.net_profit),
            net_return=D(row.net_return),
        )
        for row in rows
    ]
    eligible = [x for x in opportunities if x.risk_status == "qualified"]
    live = [x for x in eligible if x.expires_at > now()]
    latest = {(x.match_id, x.first_outcome): x for x in sorted(live, key=lambda x: x.detected_at)}
    hedged = [
        x
        for x in daily
        if x.state in (PaperState.HEDGED, PaperState.SETTLED)
        and x.unhedged_quantity == 0
        and x.first.quantity > 0
    ]
    filled = [x for x in daily if x.first.quantity or x.second.quantity]
    expected = sum((x.expected_profit for x in daily), D("0"))
    locked = sum((x.locked_profit or D("0") for x in daily), D("0"))
    actual_capital = sum(
        (x.first.cost + x.second.cost + x.first.fee + x.second.fee for x in daily), D("0")
    )
    fees = sum((x.first.fee + x.second.fee for x in daily), D("0"))
    risk, revision = await store.risk()
    exposure = exposure_for(trades, "", "", now())
    by_league: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "profit": D("0")})
    by_direction: dict[str, int] = defaultdict(int)
    for op in eligible:
        by_league[op.league]["count"] += 1
        by_direction[f"K {op.first_outcome} / {op.second_venue} {op.second_outcome}"] += 1
    for trade in daily:
        by_league[trade.league]["profit"] += trade.locked_profit or D("0")
    series = []
    for hour in range(24):
        hourly_ops = [x for x in eligible if x.detected_at.hour == hour]
        hourly_trades = [x for x in daily if x.decision_at.hour == hour]
        series.append(
            {
                "hour": f"{hour:02}:00",
                "count": len(hourly_ops),
                "expected": str(sum((x.expected_profit for x in hourly_trades), D("0"))),
                "simulated": str(sum((x.locked_profit or D("0") for x in hourly_trades), D("0"))),
            }
        )
    unique_matches = {(x.first_market_id, x.second_market_id) for x in opportunities}
    return {
        "day": selected,
        "source": store.source,
        "label": "Hypothetical paper results - not earnings",
        "active_opportunities": len(latest),
        "detected": len(opportunities),
        "qualified": len(eligible),
        "matching_passed_pairs": len(unique_matches),
        "paper_trades": len(daily),
        "fully_hedged": len(hedged),
        "partial_or_failed": len(daily) - len(hedged),
        "unhedged": sum(x.unhedged_quantity > 0 for x in daily),
        "expected_profit": str(expected),
        "simulated_locked_profit": str(locked),
        "settlement_profit": str(
            sum(
                (
                    x.settlement_profit or D("0")
                    for x in trades
                    if x.settlement_at and x.settlement_at.date().isoformat() == selected
                ),
                D("0"),
            )
        ),
        "requested_capital": str(risk.max_per_venue * 2 * len(daily)),
        "actual_capital": str(actual_capital),
        "committed_capital": str(exposure.committed),
        "fees": str(fees),
        "fill_success_rate": str(D(len(hedged)) / len(daily) if daily else D("0")),
        "partial_fill_rate": str(
            D(
                sum(
                    bool(x.first.quantity or x.second.quantity)
                    and (x.first.quantity < x.quantity or x.second.quantity < x.quantity)
                    for x in daily
                )
            )
            / len(daily)
            if daily
            else D("0")
        ),
        "average_net_edge": str(
            sum((x.net_return for x in eligible), D("0")) / len(eligible) if eligible else D("0")
        ),
        "average_simulated_edge": str(locked / actual_capital if actual_capital else D("0")),
        "rejections": len(rejection_rows),
        "rejection_reasons": dict(Counter(rejection_rows)),
        "series": series,
        "by_league": {k: {**v, "profit": str(v["profit"])} for k, v in by_league.items()},
        "by_direction": dict(by_direction),
        "quote_ages_ms": [x.quote_age_ms for x in eligible],
        "sizes": [str(x.quantity) for x in eligible],
        "largest_opportunity": str(max((x.net_profit for x in eligible), default=D("0"))),
        "slippage": str(
            sum(
                ((x.first.cost + x.second.cost) * x.settings.slippage_bps / 10000 for x in filled),
                D("0"),
            )
        ),
        "risk_revision": revision,
        "kill_switch": risk.kill_switch,
        "reporting_target": risk.reporting_target,
        "venue_health": health,
        "workers": workers,
        "data_completeness": "demo"
        if store.source == "demo"
        else (
            "partial"
            if len(health) < 3 or any(x.get("discovery") != "complete" for x in health)
            else "observed public coverage; monitored universe bounded"
        ),
    }


async def aggregate_day(store: Store, day: str) -> None:
    from app.store import upsert

    payload = await summary(store, day)
    async with store.sessions.begin() as session:
        await upsert(
            session,
            AggregateRow,
            f"{store.source}:{day}",
            source=store.source,
            day=day,
            payload=payload,
        )

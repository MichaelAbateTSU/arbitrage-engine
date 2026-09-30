import asyncio
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from app.arbitrage import detect, exposure_reasons
from app.db import CurrentBookRow, MatchRow, OpportunityRow, PaperTradeRow, RiskRow
from app.domain import (
    Book,
    Market,
    Match,
    Opportunity,
    PaperState,
    PaperTrade,
    RiskSettings,
    Side,
    fingerprint,
    now,
)
from app.matching import match_markets
from app.paper import evaluate_paper, settle, transition
from app.store import ACTIVE_PAPER, Store, audit, exposure_for
from app.telemetry import DECISIONS


async def rematch_all(store: Store) -> int:
    markets = await store.markets()
    first = [x for x in markets if x.venue == "kalshi"]
    second = [x for x in markets if x.venue != "kalshi"]
    count = 0
    for a in first:
        for b in second:
            if a.league != b.league:
                continue
            if not a.participants or not b.participants:
                continue
            if set(a.participants) != set(b.participants):
                continue
            if a.start_time and b.start_time and a.start_time.date() != b.start_time.date():
                continue
            match = match_markets(a, b)
            await store.save_match(match, a)
            count += 1
    return count


async def reserve_trade(store: Store, opportunity: Opportunity) -> bool:
    async with store.sessions.begin() as session:
        risk_row = await session.get(RiskRow, store.source, with_for_update=True)
        if risk_row is None:
            raise RuntimeError("RISK_SETTINGS_NOT_INITIALIZED")
        settings = RiskSettings.model_validate(risk_row.payload)
        if settings.kill_switch or opportunity.expires_at < now():
            return False
        if await session.scalar(
            select(PaperTradeRow.id).where(PaperTradeRow.opportunity_id == opportunity.id)
        ):
            return False
        match_row = await session.get(MatchRow, opportunity.match_id)
        if match_row is None or not match_row.current or match_row.status != "approved":
            return False
        rows = (
            await session.scalars(select(PaperTradeRow).where(PaperTradeRow.source == store.source))
        ).all()
        trades = [PaperTrade.model_validate(row.payload) for row in rows]
        # A persistent event-level reservation prevents reusing the same liquidity
        # on every book update, including an uncertain/one-sided simulated fill.
        if any(x.event_id == opportunity.event_id and x.state in ACTIVE_PAPER for x in trades):
            return False
        exposure = exposure_for(trades, opportunity.event_id, opportunity.league, now())
        a = Market.model_validate(opportunity.inputs["first_market"])
        b = Market.model_validate(opportunity.inputs["second_market"])
        book_rows = (
            await session.scalars(
                select(CurrentBookRow).where(
                    CurrentBookRow.market_id.in_([a.id, b.id]),
                    CurrentBookRow.source == store.source,
                )
            )
        ).all()
        books = {
            (row.market_id, Side(row.outcome)): Book.model_validate(row.payload)
            for row in book_rows
        }
        fresh, _ = detect(
            Match.model_validate(match_row.payload), a, b, books, settings, now(), exposure
        )
        current = next((x for x in fresh if x.id == opportunity.id), None)
        if current is None or current.risk_status != "qualified":
            return False
        c = opportunity.calculation
        capital = c.cost_one + c.cost_two + c.fee_one + c.fee_two + c.slippage + c.safety_buffer
        if exposure_reasons(settings, exposure, capital):
            return False
        at = now()
        trade = PaperTrade(
            id=uuid4().hex,
            opportunity_id=opportunity.id,
            event_id=opportunity.event_id,
            league=opportunity.league,
            quantity=c.quantity,
            decision_at=at,
            due_at=at + timedelta(milliseconds=settings.latency_ms),
            expected_profit=c.net_profit,
            reserved_capital=capital,
            model=settings.fill_model,
            settings=settings,
            source=store.source,
        )
        transition(trade, PaperState.READY, at)
        transition(trade, PaperState.SUBMITTED, at)
        session.add(
            PaperTradeRow(
                id=trade.id,
                source=store.source,
                opportunity_id=opportunity.id,
                event_id=trade.event_id,
                league=trade.league,
                state=trade.state,
                reserved_capital=capital,
                payload=trade.model_dump(mode="json"),
            )
        )
        audit(
            session,
            store.source,
            "paper_intent_persisted",
            "analysis",
            {
                "trade_id": trade.id,
                "opportunity_id": opportunity.id,
                "capital": str(capital),
            },
        )
        return True


async def paper_tick(store: Store) -> None:
    markets = {x.id: x for x in await store.markets()}
    books = await store.books()
    settings, _ = await store.risk()
    trades = await store.paper_trades()
    for trade in trades:
        if trade.state not in ACTIVE_PAPER:
            continue
        async with store.sessions() as session:
            op_row = await session.get(OpportunityRow, trade.opportunity_id)
            if op_row is None:
                raise RuntimeError("PAPER_INTENT_WITHOUT_OPPORTUNITY")
            opportunity = Opportunity.model_validate(op_row.payload)
        a, b = markets.get(opportunity.first_market_id), markets.get(opportunity.second_market_id)
        if a is None or b is None:
            continue
        settings, _ = await store.risk()
        previous = trade.state
        if settle(trade, opportunity, a, b, now()) or evaluate_paper(
            trade,
            opportunity,
            books,
            a,
            b,
            now(),
            settings.kill_switch,
        ):
            await store.save_trade(trade, previous)


async def analysis_tick(store: Store, *, process_paper: bool = True) -> None:
    if process_paper:
        await paper_tick(store)
    markets = {x.id: x for x in await store.markets()}
    matches = await store.matches()
    books = await store.books()
    trades = await store.paper_trades()
    settings, _ = await store.risk()
    for match in matches:
        a, b = markets.get(match.first_market_id), markets.get(match.second_market_id)
        if a is None or b is None:
            continue
        exposure = exposure_for(trades, match.event_id, a.league, now())
        opportunities, failures = await asyncio.to_thread(
            detect,
            match,
            a,
            b,
            books,
            settings,
            now(),
            exposure,
        )
        if failures:
            signature = fingerprint(
                {
                    "match": match.model_dump(),
                    "risk": settings.model_dump(),
                    "books": [
                        x.model_dump()
                        for (identifier, _), x in books.items()
                        if identifier in (a.id, b.id)
                    ],
                }
            )
            await store.reject(match, failures, signature)
        for opportunity in opportunities:
            if await store.save_opportunity(opportunity):
                DECISIONS.labels(opportunity.risk_status).inc()
                if opportunity.risk_status == "qualified":
                    await reserve_trade(store, opportunity)

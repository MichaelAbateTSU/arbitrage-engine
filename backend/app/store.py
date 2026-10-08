import time
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import (
    AlertRow,
    AliasRow,
    AnnotationRow,
    AuditRow,
    CanonicalEventRow,
    CurrentBookRow,
    HealthRow,
    MarketRow,
    MatchRow,
    ObservationRow,
    OpportunityRow,
    PaperLegRow,
    PaperTradeRow,
    Record,
    RejectionRow,
    RiskRow,
    SnapshotRow,
    SpecificationRow,
    SystemEventRow,
    ValidationConfigRow,
    WorkerRow,
)
from app.domain import (
    Book,
    D,
    Exposure,
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
from app.matching import Alias

ACTIVE_PAPER = ["CREATED", "READY", "SUBMITTED", "HEDGED", "UNHEDGED"]


async def upsert[T: Record](
    session: AsyncSession, model: type[T], identifier: str, **fields: Any
) -> T:
    row = await session.get(model, identifier)
    if row is None:
        row = model(id=identifier, **fields)
        session.add(row)
    else:
        for key, value in fields.items():
            setattr(row, key, value)
        row.updated_at = now()
    return row


def audit(
    session: AsyncSession, source: str, action: str, actor: str, payload: dict[str, Any]
) -> None:
    session.add(
        AuditRow(
            id=uuid4().hex,
            source=source,
            action=action,
            actor=actor,
            payload={**payload, "action": action, "actor": actor, "at": now().isoformat()},
        )
    )


class Store:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        self.sessions = sessions
        self.settings = settings
        self.source = settings.data_mode

    async def initialize(self) -> None:
        async with self.sessions.begin() as session:
            from app.matching import normalized
            from app.teams import builtin_aliases

            insert = sqlite_insert if session.get_bind().dialect.name == "sqlite" else pg_insert
            aliases = {}
            for alias in builtin_aliases():
                identifier = fingerprint([alias.league, normalized(alias.original), self.source])
                aliases[identifier] = dict(
                    id=identifier,
                    source=self.source,
                    league=alias.league,
                    original=normalized(alias.original),
                    payload={"alias": alias.model_dump(mode="json")},
                )
            await session.execute(
                insert(AliasRow)
                .values(list(aliases.values()))
                .on_conflict_do_nothing(index_elements=["id"])
            )
            risk = RiskSettings(kill_switch=self.settings.global_kill_switch)
            if self.settings.btc_15m_enabled:
                risk.supported_leagues.append("BTC")
            from app.validation import ValidationSettings

            await session.execute(
                insert(ValidationConfigRow)
                .values(
                    id=self.source,
                    source=self.source,
                    revision=1,
                    payload=ValidationSettings().model_dump(mode="json"),
                )
                .on_conflict_do_nothing(index_elements=["id"])
            )
            await session.execute(
                insert(RiskRow)
                .values(
                    id=self.source,
                    source=self.source,
                    revision=1,
                    payload=risk.model_dump(mode="json"),
                )
                .on_conflict_do_nothing(index_elements=["id"])
            )
            await session.execute(
                insert(WorkerRow)
                .values(
                    [
                        dict(
                            id=role,
                            source=self.source,
                            role=role,
                            owner="",
                            expires_epoch=0,
                            payload={"status": "not_started"},
                        )
                        for role in ("market-data", "analysis", "maintenance")
                    ]
                )
                .on_conflict_do_nothing(index_elements=["id"])
            )

    async def risk(self) -> tuple[RiskSettings, int]:
        async with self.sessions() as session:
            row = await session.get(RiskRow, self.source)
            if row is None:
                raise RuntimeError("RISK_SETTINGS_NOT_INITIALIZED")
            return RiskSettings.model_validate(row.payload), row.revision

    async def aliases(self) -> list[Alias]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(select(AliasRow).where(AliasRow.source == self.source))
            ).all()
            return [Alias.model_validate(row.payload["alias"]) for row in rows]

    async def save_market(self, market: Market) -> None:
        async with self.sessions.begin() as session:
            annotation = await session.get(AnnotationRow, market.id)
            if annotation and annotation.raw_rules_hash == market.public_spec_hash:
                market = Market.model_validate(
                    {
                        **market.model_dump(),
                        **annotation.payload["fields"],
                    }
                )
            await upsert(
                session,
                MarketRow,
                market.id,
                source=market.source,
                venue=market.venue,
                external_id=market.external_id,
                league=market.league,
                status=market.status,
                rules_hash=market.rules_hash,
                payload=market.model_dump(mode="json"),
            )
            await session.flush()
            version = fingerprint([market.id, market.rules_hash])
            if await session.get(SpecificationRow, version) is None:
                session.add(
                    SpecificationRow(
                        id=version,
                        source=market.source,
                        market_id=market.id,
                        rules_hash=market.rules_hash,
                        payload=market.model_dump(mode="json"),
                    )
                )

    async def markets(self, market_ids: list[str] | None = None) -> list[Market]:
        async with self.sessions() as session:
            query = select(MarketRow).where(MarketRow.source == self.source)
            if market_ids is not None:
                query = query.where(MarketRow.id.in_(market_ids))
            rows = (await session.scalars(query)).all()
            return [Market.model_validate(row.payload) for row in rows]

    async def bitcoin_markets(self, venue: str | None = None) -> list[Market]:
        async with self.sessions() as session:
            query = select(MarketRow).where(
                MarketRow.source == self.source,
                MarketRow.league == "BTC",
                MarketRow.status == "open",
            )
            if venue is not None:
                query = query.where(MarketRow.venue == venue)
            rows = (await session.scalars(query)).all()
            return [Market.model_validate(row.payload) for row in rows]

    async def save_match(self, match: Match, market: Market) -> None:
        async with self.sessions.begin() as session:
            await upsert(
                session,
                CanonicalEventRow,
                match.event_id,
                source=self.source,
                league=market.league,
                payload={
                    "id": match.event_id,
                    "league": market.league,
                    "sport": market.sport,
                    "participants": market.participants,
                    "start_time": market.start_time.isoformat() if market.start_time else None,
                },
            )
            await session.flush()
            await session.execute(
                update(MatchRow)
                .where(
                    MatchRow.first_market_id == match.first_market_id,
                    MatchRow.second_market_id == match.second_market_id,
                    MatchRow.id != match.id,
                )
                .values(current=False)
            )
            existing = await session.get(MatchRow, match.id)
            if existing:
                return
            await upsert(
                session,
                MatchRow,
                match.id,
                source=self.source,
                event_id=match.event_id,
                first_market_id=match.first_market_id,
                second_market_id=match.second_market_id,
                status=match.status,
                payload=match.model_dump(mode="json"),
                current=True,
            )

    async def matches(self, match_ids: list[str] | None = None) -> list[Match]:
        async with self.sessions() as session:
            query = select(MatchRow).where(
                MatchRow.source == self.source, MatchRow.current.is_(True)
            )
            if match_ids is not None:
                query = query.where(MatchRow.id.in_(match_ids))
            rows = (await session.scalars(query)).all()
            return [Match.model_validate(row.payload) for row in rows]

    async def save_books(self, books: list[Book]) -> None:
        if not books:
            return
        async with self.sessions.begin() as session:
            insert = sqlite_insert if session.get_bind().dialect.name == "sqlite" else pg_insert
            current_rows = []
            snapshot_rows = []
            for book in books:
                identifier = f"{book.market_id}:{book.outcome}"
                payload = book.model_dump(mode="json")
                current_rows.append(
                    dict(
                        id=identifier,
                        source=self.source,
                        market_id=book.market_id,
                        outcome=book.outcome,
                        payload=payload,
                    )
                )
                snapshot_rows.append(
                    dict(
                        id=fingerprint(payload),
                        source=self.source,
                        market_id=book.market_id,
                        outcome=book.outcome,
                        payload=payload,
                    )
                )
            statement = insert(CurrentBookRow).values(current_rows)
            stored_at = CurrentBookRow.payload["received_at"].as_string()
            incoming_at = statement.excluded.payload["received_at"].as_string()
            stored_second = func.substr(stored_at, 1, 19)
            incoming_second = func.substr(incoming_at, 1, 19)
            # UTC ISO timestamps may omit a zero fractional part; whole-string order is wrong.
            stored_fraction = case(
                (func.substr(stored_at, 20, 1) == ".", func.substr(stored_at, 21, 6)),
                else_="000000",
            )
            incoming_fraction = case(
                (func.substr(incoming_at, 20, 1) == ".", func.substr(incoming_at, 21, 6)),
                else_="000000",
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=["id"],
                    set_={"payload": statement.excluded.payload, "updated_at": now()},
                    where=or_(
                        stored_second < incoming_second,
                        and_(
                            stored_second == incoming_second, stored_fraction <= incoming_fraction
                        ),
                    ),
                )
            )
            await session.execute(
                insert(SnapshotRow)
                .values(snapshot_rows)
                .on_conflict_do_nothing(index_elements=["id"])
            )

    async def books(self, market_ids: list[str] | None = None) -> dict[tuple[str, Side], Book]:
        async with self.sessions() as session:
            query = select(CurrentBookRow).where(CurrentBookRow.source == self.source)
            if market_ids is not None:
                query = query.where(CurrentBookRow.market_id.in_(market_ids))
            rows = (await session.scalars(query)).all()
            return {
                (row.market_id, Side(row.outcome)): Book.model_validate(row.payload) for row in rows
            }

    async def invalidate_books(self, ids: list[str]) -> None:
        if not ids:
            return
        async with self.sessions.begin() as session:
            rows = (
                await session.scalars(
                    select(CurrentBookRow).where(
                        CurrentBookRow.market_id.in_(ids), CurrentBookRow.source == self.source
                    )
                )
            ).all()
            for row in rows:
                row.payload = {**row.payload, "connected": False, "synchronized": False}

    async def save_opportunity(self, opportunity: Opportunity) -> bool:
        async with self.sessions.begin() as session:
            if await session.get(OpportunityRow, opportunity.id):
                return False
            c = opportunity.calculation
            session.add(
                OpportunityRow(
                    id=opportunity.id,
                    source=self.source,
                    match_id=opportunity.match_id,
                    event_id=opportunity.event_id,
                    league=opportunity.league,
                    status=opportunity.risk_status,
                    expires_at=opportunity.expires_at,
                    net_profit=c.net_profit,
                    net_return=c.net_return,
                    direction=opportunity.first_outcome,
                    payload=opportunity.model_dump(mode="json"),
                )
            )
            session.add(
                ObservationRow(
                    id=opportunity.id,
                    source=self.source,
                    opportunity_id=opportunity.id,
                    payload={
                        "at": opportunity.detected_at.isoformat(),
                        "risk_status": opportunity.risk_status,
                        "inputs_hash": fingerprint(opportunity.inputs),
                    },
                )
            )
            for reason in opportunity.reasons:
                session.add(
                    RejectionRow(
                        id=fingerprint([opportunity.id, reason]),
                        source=self.source,
                        match_id=opportunity.match_id,
                        reason=reason,
                        payload={
                            "opportunity_id": opportunity.id,
                            "reasons": [reason],
                            "at": opportunity.detected_at.isoformat(),
                        },
                    )
                )
            return True

    async def reject(self, match: Match, reasons: list[str], inputs_hash: str) -> None:
        async with self.sessions.begin() as session:
            for reason in reasons:
                identifier = fingerprint([match.id, reason, inputs_hash])
                if await session.get(RejectionRow, identifier) is None:
                    session.add(
                        RejectionRow(
                            id=identifier,
                            source=self.source,
                            match_id=match.id,
                            reason=reason,
                            payload={
                                "match_id": match.id,
                                "reasons": [reason],
                                "at": now().isoformat(),
                                "inputs_hash": inputs_hash,
                            },
                        )
                    )

    async def paper_trades(self) -> list[PaperTrade]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(PaperTradeRow).where(PaperTradeRow.source == self.source)
                )
            ).all()
            return [PaperTrade.model_validate(row.payload) for row in rows]

    async def save_trade(self, trade: PaperTrade, expected_state: str | None = None) -> bool:
        async with self.sessions.begin() as session:
            row = await session.get(PaperTradeRow, trade.id, with_for_update=True)
            if row is None or (expected_state and row.state != expected_state):
                return False
            row.state = trade.state
            row.payload = trade.model_dump(mode="json")
            row.updated_at = now()
            for index, leg in enumerate((trade.first, trade.second), 1):
                await upsert(
                    session,
                    PaperLegRow,
                    f"{trade.id}:{index}",
                    source=self.source,
                    trade_id=trade.id,
                    leg=index,
                    payload=leg.model_dump(mode="json"),
                )
            audit(
                session,
                self.source,
                "paper_transition",
                "analysis",
                {
                    "trade_id": trade.id,
                    "state": trade.state,
                    "failure_reason": trade.failure_reason,
                },
            )
            if trade.state == PaperState.UNHEDGED:
                session.add(
                    AlertRow(
                        id=uuid4().hex,
                        source=self.source,
                        severity="critical",
                        payload={
                            "code": "PAPER_UNHEDGED",
                            "trade_id": trade.id,
                            "quantity": str(trade.unhedged_quantity),
                        },
                    )
                )
                risk_row = await session.get(RiskRow, self.source, with_for_update=True)
                if risk_row:
                    risk = RiskSettings.model_validate(risk_row.payload)
                    if trade.unhedged_quantity > risk.max_unhedged_quantity:
                        risk_row.payload = {**risk_row.payload, "kill_switch": True}
                        risk_row.revision += 1
                        audit(
                            session,
                            self.source,
                            "unhedged_circuit_breaker",
                            "analysis",
                            {
                                "trade_id": trade.id,
                                "quantity": str(trade.unhedged_quantity),
                            },
                        )
            return True

    async def health(self, venue: str, **fields: Any) -> None:
        async with self.sessions.begin() as session:
            previous = await session.get(HealthRow, venue)
            payload = dict(previous.payload) if previous else {}
            payload.update(fields)
            payload.update(venue=venue, at=now().isoformat(), source=self.source)
            await upsert(
                session,
                HealthRow,
                venue,
                source=self.source,
                venue=venue,
                payload=payload,
            )

    async def heartbeat(self, role: str, owner: str, acquire: bool = False) -> bool:
        epoch = int(time.time())
        async with self.sessions.begin() as session:
            conditions = [WorkerRow.id == role]
            if acquire:
                conditions.append(WorkerRow.expires_epoch < epoch)
            else:
                conditions.append(WorkerRow.owner == owner)
            result = await session.execute(
                update(WorkerRow)
                .where(*conditions)
                .values(
                    owner=owner,
                    expires_epoch=epoch + 30,
                    payload={
                        "role": role,
                        "status": "running",
                        "at": now().isoformat(),
                        "build": self.settings.build_version,
                        "source": self.source,
                    },
                    updated_at=now(),
                )
                .returning(WorkerRow.id)
            )
            return result.scalar_one_or_none() is not None

    async def release(self, role: str, owner: str) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                update(WorkerRow)
                .where(WorkerRow.id == role, WorkerRow.owner == owner)
                .values(
                    expires_epoch=0,
                    payload={"role": role, "status": "stopped", "at": now().isoformat()},
                )
            )

    async def system_error(self, role: str, code: str) -> None:
        async with self.sessions.begin() as session:
            session.add(
                SystemEventRow(
                    id=uuid4().hex,
                    source=self.source,
                    role=role,
                    payload={"error_code": code, "at": now().isoformat()},
                )
            )


def exposure_for(
    trades: list[PaperTrade], event_id: str, league: str, instant: datetime
) -> Exposure:
    active = [x for x in trades if x.state in ACTIVE_PAPER]
    daily = [x for x in trades if x.decision_at.date() == instant.date() and x.replay_of is None]
    losses = [
        -(x.settlement_profit or D("0"))
        for x in trades
        if x.settlement_at
        and x.settlement_at.date() == instant.date()
        and (x.settlement_profit or D("0")) < 0
    ]
    result = Exposure(
        committed=sum((x.reserved_capital for x in active), D("0")),
        daily_capital=sum((x.reserved_capital for x in daily), D("0")),
        event_capital=sum((x.reserved_capital for x in active if x.event_id == event_id), D("0")),
        league_capital=sum((x.reserved_capital for x in active if x.league == league), D("0")),
        open_trades=len(active),
        daily_trades=len(daily),
        daily_loss=sum(losses, D("0")),
        pending_executions=sum(x.state in ("CREATED", "READY", "SUBMITTED") for x in active),
        unhedged_quantity=sum((x.unhedged_quantity for x in active), D("0")),
        settled_profit=sum((x.settlement_profit or D("0") for x in trades), D("0")),
    )
    anomalies = [
        x for x in trades if x.state in (PaperState.UNHEDGED, PaperState.FAILED) and x.fill_at
    ]
    if anomalies:
        last = max(anomalies, key=lambda x: x.fill_at or x.decision_at)
        result.cooldown_until = (last.fill_at or last.decision_at) + timedelta(
            seconds=last.settings.cooldown_seconds
        )
    return result

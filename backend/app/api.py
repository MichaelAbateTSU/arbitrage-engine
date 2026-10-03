import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import field_validator
from sqlalchemy import func, select

from app.analytics import summary
from app.auth import COOKIE, Login, admin, login, rate_limit, reader, session_identity
from app.db import (
    AlertRow,
    AliasRow,
    AnnotationRow,
    AuditRow,
    CanonicalEventRow,
    HealthRow,
    MarketRow,
    MatchRow,
    OpportunityRow,
    PaperTradeRow,
    Record,
    RejectionRow,
    ReviewRow,
    RiskRow,
    WorkerRow,
)
from app.domain import (
    Market,
    Match,
    Model,
    Opportunity,
    Page,
    PaperTrade,
    RiskSettings,
    Rules,
    fingerprint,
    now,
)
from app.matching import Alias, match_markets
from app.replay import persisted_replay
from app.service import rematch_all
from app.store import Store, audit, upsert

router = APIRouter(prefix="/api/v1", dependencies=[Depends(reader)])
auth_router = APIRouter(prefix="/api/v1/auth")
Admin = Annotated[str, Depends(admin)]


class Review(Model):
    expected_first_rules_hash: str
    expected_second_rules_hash: str
    note: str = Query(default="", max_length=4000)
    scenario_coverage_acknowledged: bool = False


class RiskUpdate(Model):
    revision: int
    settings: RiskSettings
    reason: str = ""


class Annotation(Model):
    expected_rules_hash: str
    participants: list[str]
    yes_team: str
    start_time: datetime
    rules: Rules
    evidence: dict[str, str]
    note: str

    @field_validator("start_time")
    @classmethod
    def aware_start(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Scheduled start must include a timezone")
        return value.astimezone(UTC)


async def record_page(
    store: Store,
    model: type[Record],
    limit: int,
    offset: int,
    conditions: list[Any] | None = None,
) -> Page:
    where = [model.source == store.source, *(conditions or [])]
    async with store.sessions() as session:
        total = await session.scalar(select(func.count()).select_from(model).where(*where))
        rows = (
            await session.scalars(
                select(model)
                .where(*where)
                .order_by(model.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ).all()
        return Page(
            items=[{"id": row.id, **row.payload} for row in rows],
            total=int(total or 0),
            limit=limit,
            offset=offset,
        )


async def get_payload(store: Store, model: type[Record], identifier: str) -> dict[str, Any]:
    async with store.sessions() as session:
        row = await session.get(model, identifier)
        if row is None or row.source != store.source:
            raise HTTPException(404, "RECORD_NOT_FOUND")
        return {"id": row.id, **row.payload}


@auth_router.post("/login")
async def auth_login(request: Request, response: Response, body: Login) -> dict[str, Any]:
    return await login(request, response, body)


@auth_router.get("/session")
async def auth_session(request: Request) -> dict[str, Any]:
    identity = await session_identity(request)
    return {
        "authenticated": bool(identity),
        **({key: identity[key] for key in ("csrf", "actor", "expires_epoch")} if identity else {}),
    }


@auth_router.post("/logout")
async def logout(request: Request, response: Response, actor: Admin) -> dict[str, bool]:
    token = request.cookies.get(COOKIE, "")
    identifier = hashlib.sha256(token.encode()).hexdigest()
    store: Store = request.app.state.store
    async with store.sessions.begin() as session:
        from app.db import SessionRow

        row = await session.get(SessionRow, identifier)
        if row:
            await session.delete(row)
    response.delete_cookie(COOKIE, path="/")
    return {"authenticated": False}


@router.get("/markets", response_model=Page)
async def markets(
    request: Request,
    league: str | None = None,
    venue: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Page:
    conditions = []
    if league:
        conditions.append(MarketRow.league == league.upper())
    if venue:
        conditions.append(MarketRow.venue == venue)
    return await record_page(request.app.state.store, MarketRow, limit, offset, conditions)


@router.get("/markets/{identifier}/orderbook")
async def market_book(request: Request, identifier: str) -> dict[str, Any]:
    await get_payload(request.app.state.store, MarketRow, identifier)
    return {
        "items": [
            {**book.model_dump(mode="json"), "age_ms": book.age_ms(now())}
            for (market_id, _), book in (await request.app.state.store.books()).items()
            if market_id == identifier
        ]
    }


@router.get("/markets/{identifier}", response_model=Market)
async def market_detail(request: Request, identifier: str) -> Market:
    return Market.model_validate(await get_payload(request.app.state.store, MarketRow, identifier))


@router.put("/markets/{identifier}/normalization", response_model=Market)
async def annotate_market(
    request: Request,
    identifier: str,
    body: Annotation,
    actor: Admin,
) -> Market:
    store: Store = request.app.state.store
    async with store.sessions.begin() as session:
        row = await session.get(MarketRow, identifier, with_for_update=True)
        if row is None or row.source != store.source:
            raise HTTPException(404, "MARKET_NOT_FOUND")
        market = Market.model_validate(row.payload)
        if market.rules_hash != body.expected_rules_hash:
            raise HTTPException(409, "MARKET_SPECIFICATION_CHANGED")
        if len(set(body.participants)) != 2 or body.yes_team not in body.participants:
            raise HTTPException(422, "INVALID_PARTICIPANTS")
        if body.rules.unknowns:
            raise HTTPException(422, "REQUIRED_RULE_FIELDS_UNKNOWN")
        required = [
            "participants",
            "yes_team",
            "start_time",
            "period",
            "overtime",
            "draw",
            "cancellation",
            "postponement",
            "settlement_source",
        ]
        if any(not body.evidence.get(field, "").strip() for field in required):
            raise HTTPException(422, "RULE_EVIDENCE_REQUIRED")
        at = body.start_time
        fields = {
            "participants": body.participants,
            "yes_team": body.yes_team,
            "start_time": at.isoformat(),
            "start_time_verified": True,
            "rules": {**body.rules.model_dump(mode="json"), "evidence": body.evidence},
        }
        changed = Market.model_validate({**market.model_dump(mode="json"), **fields})
        await upsert(
            session,
            AnnotationRow,
            identifier,
            source=store.source,
            market_id=identifier,
            raw_rules_hash=market.public_spec_hash,
            payload={"fields": fields, "actor": actor, "note": body.note},
        )
        row.payload = changed.model_dump(mode="json")
        row.rules_hash = changed.rules_hash
        audit(
            session,
            store.source,
            "market_normalization",
            actor,
            {
                "market_id": identifier,
                "previous": market.model_dump(mode="json"),
                "new": changed.model_dump(mode="json"),
                "note": body.note,
            },
        )
    await store.save_market(changed)
    await rematch_all(store)
    return changed


@router.get("/events", response_model=Page)
async def events(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Page:
    return await record_page(request.app.state.store, CanonicalEventRow, limit, offset)


@router.get("/events/{identifier}")
async def event_detail(request: Request, identifier: str) -> dict[str, Any]:
    return await get_payload(request.app.state.store, CanonicalEventRow, identifier)


@router.get("/matches/review-queue", response_model=Page)
async def review_queue(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Page:
    return await record_page(
        request.app.state.store,
        MatchRow,
        limit,
        offset,
        [MatchRow.current.is_(True), MatchRow.status == "review"],
    )


@router.get("/matches", response_model=Page)
async def matches(
    request: Request,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Page:
    conditions: list[Any] = [MatchRow.current.is_(True)]
    if status:
        conditions.append(MatchRow.status == status)
    return await record_page(request.app.state.store, MatchRow, limit, offset, conditions)


@router.get("/matches/{identifier}")
async def match_detail(request: Request, identifier: str) -> dict[str, Any]:
    from app.validation import settlement_proof

    store: Store = request.app.state.store
    match = Match.model_validate(await get_payload(store, MatchRow, identifier))
    reviews = await record_page(store, ReviewRow, 100, 0, [ReviewRow.match_id == identifier])
    a = await get_payload(store, MarketRow, match.first_market_id)
    b = await get_payload(store, MarketRow, match.second_market_id)
    return {
        **match.model_dump(mode="json"),
        "first_market": a,
        "second_market": b,
        "first_rules_hash": Market.model_validate(a).rules_hash,
        "second_rules_hash": Market.model_validate(b).rules_hash,
        "reviews": reviews.items,
        "settlement_proof": settlement_proof(Market.model_validate(a), Market.model_validate(b)),
    }


@router.post("/matches/{identifier}/{action}", response_model=Match)
async def review_match(
    request: Request,
    identifier: str,
    action: Literal["approve", "reject", "rematch"],
    body: Review,
    actor: Admin,
) -> Match:
    store: Store = request.app.state.store
    async with store.sessions.begin() as session:
        row = await session.get(MatchRow, identifier, with_for_update=True)
        if row is None or row.source != store.source or not row.current:
            raise HTTPException(404, "CURRENT_MATCH_NOT_FOUND")
        a_row = await session.get(MarketRow, row.first_market_id)
        b_row = await session.get(MarketRow, row.second_market_id)
        if a_row is None or b_row is None:
            raise HTTPException(409, "MATCH_MARKETS_UNAVAILABLE")
        a, b = Market.model_validate(a_row.payload), Market.model_validate(b_row.payload)
        if (
            a.rules_hash != body.expected_first_rules_hash
            or b.rules_hash != body.expected_second_rules_hash
        ):
            raise HTTPException(409, "MARKET_SPECIFICATION_CHANGED")
        match = match_markets(a, b, human_reviewed=action == "approve")
        if match.id != identifier:
            raise HTTPException(409, "REMATCH_NEW_SPECIFICATION_REQUIRED")
        if action == "approve" and match.status != "approved":
            raise HTTPException(
                422, {"code": "DETERMINISTIC_GATES_FAILED", "reasons": match.reasons}
            )
        if action == "approve":
            from app.validation import settlement_proof

            proof = settlement_proof(a, b)
            if (
                not body.scenario_coverage_acknowledged
                or not proof["proven"]
                or not body.note.strip()
            ):
                raise HTTPException(422, "SETTLEMENT_SCENARIO_REVIEW_AND_NOTE_REQUIRED")
        if action == "reject":
            match.status = "rejected"
            match.reasons = ["HUMAN_REJECTED"]
        row.payload = match.model_dump(mode="json")
        row.status = match.status
        session.add(
            ReviewRow(
                id=uuid4().hex,
                source=store.source,
                match_id=identifier,
                actor=actor,
                payload={
                    "action": action,
                    "note": body.note,
                    "at": now().isoformat(),
                    "scenario_coverage_acknowledged": body.scenario_coverage_acknowledged,
                },
            )
        )
        audit(
            session,
            store.source,
            f"match_{action}",
            actor,
            {
                "match_id": identifier,
                "note": body.note,
                "rules_hashes": [a.rules_hash, b.rules_hash],
            },
        )
        return match


@router.get("/opportunities/stats")
@router.get("/paper-trades/stats")
@router.get("/paper-trades/daily-summary")
@router.get("/analytics")
@router.get("/system/metrics-summary")
async def stats(request: Request, day: str | None = None) -> dict[str, Any]:
    if day:
        try:
            from datetime import date

            date.fromisoformat(day)
        except ValueError as exc:
            raise HTTPException(422, "INVALID_DAY") from exc
    return await summary(request.app.state.store, day)


@router.get("/opportunities/rejections", response_model=Page)
async def rejections(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Page:
    return await record_page(request.app.state.store, RejectionRow, limit, offset)


@router.get("/opportunities/live", response_model=Page)
@router.get("/opportunities", response_model=Page)
async def opportunities(
    request: Request,
    league: str | None = None,
    min_edge: Decimal | None = None,
    min_profit: Decimal | None = None,
    max_age: int | None = Query(None, ge=0),
    direction: str | None = None,
    active: bool = False,
    sort: Literal["detected", "profit", "edge"] = "detected",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Page:
    store: Store = request.app.state.store
    conditions: list[Any] = [OpportunityRow.source == store.source]
    if league:
        conditions.append(OpportunityRow.league == league.upper())
    if min_edge is not None:
        conditions.append(OpportunityRow.net_return >= min_edge)
    if min_profit is not None:
        conditions.append(OpportunityRow.net_profit >= min_profit)
    if max_age is not None:
        conditions.append(OpportunityRow.created_at >= now() - timedelta(seconds=max_age))
    if direction:
        conditions.append(OpportunityRow.direction == direction.upper())
    if active or request.url.path.endswith("/live"):
        conditions += [OpportunityRow.expires_at > now(), OpportunityRow.status == "qualified"]
    column = {
        "detected": OpportunityRow.created_at,
        "profit": OpportunityRow.net_profit,
        "edge": OpportunityRow.net_return,
    }[sort]
    async with store.sessions() as session:
        total = await session.scalar(
            select(func.count()).select_from(OpportunityRow).where(*conditions)
        )
        rows = (
            await session.scalars(
                select(OpportunityRow)
                .where(*conditions)
                .order_by(column.desc())
                .offset(offset)
                .limit(limit)
            )
        ).all()
        return Page(
            items=[x.payload for x in rows], total=int(total or 0), limit=limit, offset=offset
        )


@router.get("/opportunities/{identifier}", response_model=Opportunity)
async def opportunity_detail(request: Request, identifier: str) -> Opportunity:
    return Opportunity.model_validate(
        await get_payload(request.app.state.store, OpportunityRow, identifier)
    )


@router.get("/paper-trades", response_model=Page)
async def trades(
    request: Request,
    state: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Page:
    return await record_page(
        request.app.state.store,
        PaperTradeRow,
        limit,
        offset,
        [PaperTradeRow.state == state] if state else [],
    )


@router.get("/paper-trades/{identifier}", response_model=PaperTrade)
async def trade_detail(request: Request, identifier: str) -> PaperTrade:
    return PaperTrade.model_validate(
        await get_payload(request.app.state.store, PaperTradeRow, identifier)
    )


@router.post("/paper-trades/{identifier}/replay")
async def replay(request: Request, identifier: str, actor: Admin) -> dict[str, Any]:
    store: Store = request.app.state.store
    await rate_limit(store, f"replay:{actor}", 10, 60)
    trade = PaperTrade.model_validate(await get_payload(store, PaperTradeRow, identifier))
    opportunity = Opportunity.model_validate(
        await get_payload(store, OpportunityRow, trade.opportunity_id)
    )
    return await persisted_replay(store, trade, opportunity)


@router.get("/settings/risk")
async def get_risk(request: Request) -> dict[str, Any]:
    risk, revision = await request.app.state.store.risk()
    return {"revision": revision, "settings": risk.model_dump(mode="json")}


@router.put("/settings/risk")
async def set_risk(request: Request, body: RiskUpdate, actor: Admin) -> dict[str, Any]:
    store: Store = request.app.state.store
    async with store.sessions.begin() as session:
        row = await session.get(RiskRow, store.source, with_for_update=True)
        if row is None:
            raise HTTPException(503, "RISK_SETTINGS_UNAVAILABLE")
        if row.revision != body.revision:
            raise HTTPException(409, "SETTINGS_REVISION_CONFLICT")
        previous = row.payload
        row.payload = body.settings.model_dump(mode="json")
        row.revision += 1
        audit(
            session,
            store.source,
            "risk_settings_update",
            actor,
            {
                "previous": previous,
                "new": row.payload,
                "revision": row.revision,
                "reason": body.reason,
            },
        )
        return {"revision": row.revision, "settings": row.payload}


@router.post("/settings/kill-switch/{action}")
async def kill_switch(
    request: Request,
    action: Literal["activate", "deactivate"],
    actor: Admin,
) -> dict[str, Any]:
    store: Store = request.app.state.store
    async with store.sessions.begin() as session:
        row = await session.get(RiskRow, store.source, with_for_update=True)
        if row is None:
            raise HTTPException(503, "RISK_SETTINGS_UNAVAILABLE")
        previous = row.payload
        row.payload = {**previous, "kill_switch": action == "activate"}
        row.revision += 1
        audit(
            session,
            store.source,
            "kill_switch",
            actor,
            {
                "previous": previous["kill_switch"],
                "new": row.payload["kill_switch"],
            },
        )
        if action == "activate":
            session.add(
                AlertRow(
                    id=uuid4().hex,
                    source=store.source,
                    severity="warning",
                    payload={
                        "code": "KILL_SWITCH_ACTIVATED",
                        "actor": actor,
                        "at": now().isoformat(),
                    },
                )
            )
        return {"revision": row.revision, "settings": row.payload}


@router.get("/settings/aliases", response_model=Page)
async def aliases(request: Request) -> Page:
    return await record_page(request.app.state.store, AliasRow, 200, 0)


@router.put("/settings/aliases")
async def set_alias(request: Request, body: Alias, actor: Admin) -> dict[str, bool]:
    store: Store = request.app.state.store
    from app.matching import normalized

    body.original = normalized(body.original)
    identifier = fingerprint([body.league, body.original, store.source])
    async with store.sessions.begin() as session:
        previous = await session.get(AliasRow, identifier)
        audit(
            session,
            store.source,
            "alias_update",
            actor,
            {
                "previous": previous.payload if previous else None,
                "new": body.model_dump(mode="json"),
            },
        )
        await upsert(
            session,
            AliasRow,
            identifier,
            source=store.source,
            league=body.league,
            original=body.original,
            payload={"alias": body.model_dump(mode="json"), "at": now().isoformat()},
        )
    return {"saved": True}


@router.get("/system/venues", response_model=Page)
async def venues(request: Request) -> Page:
    return await record_page(request.app.state.store, HealthRow, 50, 0)


@router.get("/system/workers")
async def workers(request: Request) -> dict[str, Any]:
    store: Store = request.app.state.store
    async with store.sessions() as session:
        rows = (
            await session.scalars(select(WorkerRow).where(WorkerRow.source == store.source))
        ).all()
        return {
            "items": [
                {
                    **row.payload,
                    "role": row.role,
                    "healthy": row.expires_epoch > time.time(),
                    "heartbeat_age_seconds": max(0, int(time.time()) - (row.expires_epoch - 30)),
                }
                for row in rows
            ]
        }


@router.get("/system/health")
async def system_health(request: Request) -> dict[str, Any]:
    return {
        "venues": (await venues(request)).items,
        "workers": (await workers(request))["items"],
        "dependencies": await request.app.state.dependencies(),
    }


@router.get("/system/configuration")
async def configuration(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    return {
        "environment": settings.environment,
        "data_mode": settings.data_mode,
        "trading_mode": "paper",
        "live_execution_available": False,
        "build_version": settings.build_version,
        "kalshi_websocket_configured": bool(
            settings.kalshi_api_key and settings.kalshi_private_key
        ),
        "polymarket_us_websocket_configured": bool(
            settings.polymarket_us_key_id and settings.polymarket_us_secret_key
        ),
        "redis_configured": bool(settings.redis_url),
        "admin_configured": bool(settings.admin_password_hash),
        "max_monitored_markets_per_venue": settings.max_monitored_markets,
    }


@router.get("/audit", response_model=Page)
async def audits(
    request: Request,
    actor: Admin,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Page:
    return await record_page(request.app.state.store, AuditRow, limit, offset)


@router.get("/alerts", response_model=Page)
async def alerts(request: Request) -> Page:
    return await record_page(request.app.state.store, AlertRow, 100, 0)


@router.post("/alerts/{identifier}/acknowledge")
async def acknowledge(request: Request, identifier: str, actor: Admin) -> dict[str, bool]:
    store: Store = request.app.state.store
    async with store.sessions.begin() as session:
        row = await session.get(AlertRow, identifier)
        if row is None or row.source != store.source:
            raise HTTPException(404, "ALERT_NOT_FOUND")
        row.acknowledged = True
        audit(session, store.source, "alert_acknowledged", actor, {"alert_id": identifier})
    return {"acknowledged": True}


@router.get("/stream")
async def stream(request: Request) -> StreamingResponse:
    async def events() -> Any:
        previous = ""
        while not await request.is_disconnected():
            identity = await session_identity(request)
            settings = request.app.state.settings
            if (
                settings.environment == "production"
                and not settings.public_read_enabled
                and not identity
            ):
                yield "event: auth_required\ndata: {}\n\n"
                return
            payload = {
                "at": now().isoformat(),
                "source": settings.data_mode,
                "workers": (await workers(request))["items"],
                "venues": (await venues(request)).items,
            }
            text = json.dumps(payload)
            if text != previous:
                yield f"event: update\ndata: {text}\n\n"
                previous = text
            yield ": heartbeat\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/metrics")
async def metrics(actor: Admin) -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

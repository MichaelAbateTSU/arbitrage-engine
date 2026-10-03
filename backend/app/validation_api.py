from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select

from app.api import Admin, get_payload, record_page
from app.auth import reader
from app.db import EligibilityRow, EpisodeRow, ShadowRow, ValidationConfigRow, ValidationRow
from app.domain import Model, Venue, now
from app.eligibility import AccountAttestation, eligibility_status
from app.focused import focused_report
from app.store import Store, audit, upsert
from app.validation import ValidationSettings, validation_report, validation_settings

router = APIRouter(prefix="/api/v1/validation", dependencies=[Depends(reader)])


class ConfigurationUpdate(Model):
    revision: int
    settings: ValidationSettings
    reason: str


@router.get("/summary")
async def report(request: Request) -> dict[str, Any]:
    return await validation_report(request.app.state.store)


@router.get("/focused")
async def focused(request: Request) -> dict[str, Any]:
    return await focused_report(request.app.state.store)


@router.get("/candidates")
async def candidates(
    request: Request,
    reason: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    store: Store = request.app.state.store
    async with store.sessions() as session:
        rows = (
            await session.scalars(
                select(ValidationRow)
                .where(ValidationRow.source == store.source)
                .order_by(ValidationRow.match_id, ValidationRow.direction)
            )
        ).all()
    values = [row.payload for row in rows]
    if reason:
        values = [
            value
            for value in values
            if reason
            in {
                *value["reasons"],
                *value["execution_reasons"],
                *value["shadow_reasons"],
            }
        ]
    return {
        "items": values[offset : offset + limit],
        "total": len(values),
        "limit": limit,
        "offset": offset,
    }


@router.get("/candidates/{identifier}")
async def candidate(request: Request, identifier: str) -> dict[str, Any]:
    return await get_payload(request.app.state.store, ValidationRow, identifier)


@router.get("/episodes")
async def episodes(
    request: Request, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
) -> Any:
    return await record_page(request.app.state.store, EpisodeRow, limit, offset)


@router.get("/shadow-trials")
async def trials(
    request: Request, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
) -> Any:
    return await record_page(request.app.state.store, ShadowRow, limit, offset)


@router.get("/configuration")
async def configuration(request: Request) -> dict[str, Any]:
    settings, revision, started = await validation_settings(request.app.state.store)
    return {
        "settings": settings.model_dump(mode="json"),
        "revision": revision,
        "started_at": started.isoformat(),
    }


@router.put("/configuration")
async def set_configuration(
    request: Request, body: ConfigurationUpdate, actor: Admin
) -> dict[str, Any]:
    store: Store = request.app.state.store
    if not body.reason.strip():
        raise HTTPException(422, "VALIDATION_CHANGE_REASON_REQUIRED")
    if body.settings.operating_cost_verified and not body.settings.operating_cost_evidence.strip():
        raise HTTPException(422, "OPERATING_COST_EVIDENCE_REQUIRED")
    matches = {match.id for match in await store.matches()}
    if any(identifier not in matches for identifier in body.settings.selected_match_ids):
        raise HTTPException(422, "CURRENT_MATCH_REQUIRED_FOR_WATCHLIST")
    if len(body.settings.selected_match_ids) > 10 or body.settings.shortlist_size > 10:
        raise HTTPException(422, "FOCUSED_WATCHLIST_MAXIMUM_TEN")
    if not body.settings.focus_automatic:
        body.settings.focus_scope = "operator_selected"
    async with store.sessions.begin() as session:
        row = await session.get(ValidationConfigRow, store.source, with_for_update=True)
        if row is None:
            raise HTTPException(503, "VALIDATION_CONFIGURATION_UNAVAILABLE")
        if row.revision != body.revision:
            raise HTTPException(409, "VALIDATION_REVISION_CONFLICT")
        previous = row.payload
        row.payload = body.settings.model_dump(mode="json")
        row.revision += 1
        audit(
            session,
            store.source,
            "validation_configuration",
            actor,
            {"previous": previous, "new": row.payload, "reason": body.reason},
        )
        return {"settings": row.payload, "revision": row.revision}


@router.get("/eligibility")
async def eligibility(request: Request) -> dict[str, Any]:
    return {
        "items": [
            item.model_dump(mode="json")
            for item in (await eligibility_status(request.app.state.store)).values()
        ]
    }


@router.put("/eligibility/{venue}")
async def attest(
    request: Request,
    venue: Venue,
    body: AccountAttestation,
    actor: Admin,
) -> dict[str, Any]:
    store: Store = request.app.state.store
    if (
        venue == Venue.INTERNATIONAL
        and store.settings.operator_country.upper() == "US"
        and (body.jurisdiction_confirmed or body.order_permission_confirmed)
    ):
        raise HTTPException(422, "INTERNATIONAL_CLOSE_ONLY_FOR_US_OPERATOR")
    identifier = f"{store.source}:{venue}"
    async with store.sessions.begin() as session:
        row = await session.get(EligibilityRow, identifier, with_for_update=True)
        payload = {
            **(row.payload if row else {}),
            "attestation": body.model_dump(mode="json"),
            "actor": actor,
            "at": now().isoformat(),
        }
        await upsert(
            session, EligibilityRow, identifier, source=store.source, venue=venue, payload=payload
        )
        audit(
            session,
            store.source,
            "account_eligibility_attestation",
            actor,
            {"venue": venue, "attestation": body.model_dump(mode="json")},
        )
    return (await eligibility_status(store))[venue].model_dump(mode="json")

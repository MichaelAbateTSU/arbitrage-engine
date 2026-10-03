"""Candidate diagnostics never turn unapproved contracts into executable opportunities."""

import asyncio
from collections import Counter
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field, model_validator
from sqlalchemy import select

from app.arbitrage import calculate, exposure_reasons
from app.db import (
    EpisodeRow,
    ShadowRow,
    ValidationConfigRow,
    ValidationRow,
)
from app.domain import (
    AdditionalCosts,
    Book,
    Calculation,
    D,
    Market,
    Match,
    Model,
    Nonnegative,
    Positive,
    RiskSettings,
    Side,
    Venue,
    fingerprint,
    now,
)
from app.eligibility import Eligibility, eligibility_status
from app.focused import choose_focus, family_screen, focused_report, persist_families
from app.matching import match_markets
from app.pricing import book_reasons, quantity
from app.settlement import SCREEN_VERSION, settlement_proof
from app.store import Store, audit, exposure_for


class ValidationSettings(Model):
    enabled: bool = True
    shadow_enabled: bool = True
    shortlist_size: int = Field(default=5, ge=5, le=20)
    diagnostic_days: int = Field(default=14, ge=7, le=14)
    selected_match_ids: list[str] = Field(default_factory=list, max_length=20)
    legacy_selected_match_ids: list[str] = Field(default_factory=list, max_length=20)
    focus_version: str = ""
    focus_automatic: bool = True
    focus_scope: Literal["diagnostic_only", "potentially_eligible_review", "operator_selected"] = (
        "diagnostic_only"
    )
    virtual_balance_per_venue: Positive = D("500")
    max_shadow_per_leg: Positive = D("25")
    max_shadow_daily_trials: int = Field(default=10, ge=1, le=100)
    max_shadow_daily_loss: Positive = D("25")
    unwind_latency_ms: int = Field(default=600, ge=100, le=60000)
    episode_gap_seconds: int = Field(default=60, ge=10, le=3600)
    daily_operating_cost: Nonnegative = D("0")
    operating_cost_verified: bool = False
    operating_cost_evidence: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def consistent(self) -> "ValidationSettings":
        if len(set(self.selected_match_ids)) != len(self.selected_match_ids):
            raise ValueError("Selected pairs must be unique")
        if self.operating_cost_verified and not self.operating_cost_evidence.strip():
            raise ValueError("Operating costs require evidence")
        if self.max_shadow_per_leg > self.virtual_balance_per_venue:
            raise ValueError("Shadow leg cap exceeds virtual venue balance")
        return self


class CandidateValidation(Model):
    id: str
    match_id: str
    event_id: str
    event: str
    league: str
    second_venue: Venue
    first_market_id: str
    second_market_id: str
    first_outcome: Side
    second_outcome: Side
    approval_status: str
    human_reviewed: bool
    at: datetime
    reasons: list[str]
    execution_reasons: list[str]
    shadow_reasons: list[str]
    shadow_qualified: bool
    executable_for_operator: bool
    price_basis: str
    first_ask: D | None
    second_ask: D | None
    available_quantity: D
    first_book_age_ms: int | None
    second_book_age_ms: int | None
    calculation: Calculation | None
    settlement_proof: dict[str, Any]
    fee_evidence: dict[str, Any]
    inputs: dict[str, Any]
    source: Literal["demo", "public"]
    settlement_adjusted_net_profit: D | None = None
    known_scenario_net_floor: D | None = None


def diagnose(
    match: Match,
    a: Market,
    b: Market,
    books: dict[tuple[str, Side], Book],
    risk: RiskSettings,
    eligibility: dict[Venue, Eligibility],
    instant: datetime,
    exposure: Any,
) -> list[CandidateValidation]:
    current = match_markets(a, b, match.human_reviewed)
    proof = settlement_proof(a, b)
    common = []
    if match.status != "approved" or current.status != "approved":
        common += ["MARKET_MATCH_UNAPPROVED", *match.reasons, *current.reasons]
    if [match.first_rules_hash, match.second_rules_hash] != [a.rules_hash, b.rules_hash]:
        common.append("MARKET_SPECIFICATION_CHANGED")
    if not proof["proven"]:
        common.append("SETTLEMENT_SCENARIO_COVERAGE_UNPROVEN")
    if (risk.require_human_review or a.source == "public") and not match.human_reviewed:
        common.append("HUMAN_REVIEW_REQUIRED")
    if match.confidence < risk.min_confidence:
        common.append("MATCH_CONFIDENCE_TOO_LOW")
    if a.quote_currency != b.quote_currency and not risk.allow_usdc_parity_assumption:
        common.append("CURRENCY_ASSUMPTION_UNACKNOWLEDGED")
    if not (a.tradable and b.tradable and a.status == b.status == "open"):
        common.append("MARKET_NOT_TRADABLE")
    if any(m.instrument_mapping_error() for m in (a, b)):
        common.append("INSTRUMENT_MAPPING_INVALID")
    if a.league not in risk.supported_leagues:
        common.append("SPORT_DISABLED")
    if any(m.id in risk.blocked_markets for m in (a, b)):
        common.append("MARKET_BLOCKLISTED")
    if any(m.venue in risk.venue_kill_switches for m in (a, b)):
        common.append("VENUE_KILL_SWITCH")
    known_fees = all(m.fee.known_at(instant) for m in (a, b))
    if not known_fees:
        common.append("UNKNOWN_OR_EXPIRED_FEE")
    if a.source == "public":
        for m in (a, b):
            unknown = risk.additional_costs.get(m.venue, AdditionalCosts()).unknown_components(
                instant
            )
            if unknown:
                common += [
                    "ADDITIONAL_COSTS_UNVERIFIED",
                    *[f"COST_UNKNOWN_{m.venue.upper()}_{name.upper()}" for name in unknown],
                ]
    results = []
    for side in Side:
        other = side if match.inverted else side.opposite
        one, two = books.get((a.id, side)), books.get((b.id, other))
        reasons = list(common)
        calc = None
        available = D("0")
        if one is None or two is None:
            reasons.append("MISSING_BOOK")
        else:
            available = min(quantity(one.asks), quantity(two.asks))
            reasons += book_reasons(one, a, instant, risk.max_quote_age_ms)
            reasons += book_reasons(two, b, instant, risk.max_quote_age_ms)
            if abs((one.received_at - two.received_at).total_seconds() * 1000) > (
                risk.max_book_skew_ms
            ):
                reasons.append("BOOK_TIME_SKEW")
            if known_fees:
                try:
                    calc = calculate(one, two, a, b, risk)
                except ValueError as exc:
                    if str(exc) != "QUANTITY_ROUNDING_MISMATCH":
                        raise
                    reasons.append("QUANTITY_ROUNDING_MISMATCH")
            if (known_fees and calc is None) or available <= 0:
                reasons.append("INSUFFICIENT_DEPTH_OR_CAPITAL")
        execution = list(dict.fromkeys(eligibility[a.venue].reasons + eligibility[b.venue].reasons))
        minimum = proof["direction_bounds"][str(side)]["minimum_combined_payout"]
        known_floor = proof["direction_bounds"][str(side)]["known_scenario_floor"]
        adjusted = (
            calc.net_profit + calc.quantity * (D(minimum) - 1)
            if calc and minimum is not None
            else None
        )
        known_net = (
            calc.net_profit + calc.quantity * (D(known_floor) - 1)
            if calc and known_floor is not None
            else None
        )
        if adjusted is not None and adjusted <= 0:
            reasons.append("NONPOSITIVE_WORST_CASE_SETTLEMENT_PROFIT")
        if risk.kill_switch:
            execution.append("KILL_SWITCH_ACTIVE")
        if calc:
            if calc.gross_profit <= 0:
                reasons.append("NO_GROSS_SPREAD_AT_SIZE")
            elif calc.gross_profit <= calc.fee_one + calc.fee_two:
                reasons.append("FEES_EXCEED_SPREAD")
            if calc.net_profit < risk.min_profit or calc.net_profit <= 0:
                reasons.append("BELOW_MINIMUM_DOLLAR_PROFIT")
            if calc.net_return < risk.min_edge:
                reasons.append("BELOW_MINIMUM_NET_EDGE")
            capital = (
                calc.cost_one
                + calc.cost_two
                + calc.fee_one
                + calc.fee_two
                + calc.slippage
                + calc.safety_buffer
                + calc.additional_cost_one
                + calc.additional_cost_two
            )
            if any(not eligibility[venue].open_order_eligible for venue in (a.venue, b.venue)):
                execution.append("ORDER_PERMISSION_UNVERIFIED")
            reasons += exposure_reasons(risk, exposure, capital)
            for market, leg_cost in (
                (a, calc.cost_one + calc.fee_one + calc.additional_cost_one),
                (b, calc.cost_two + calc.fee_two + calc.additional_cost_two),
            ):
                balance = eligibility[market.venue].available_balance
                if balance is not None and balance < leg_cost:
                    execution.append(f"INSUFFICIENT_BALANCE_{market.venue.upper()}")
        shadow_reasons = list(reasons)
        if b.venue == Venue.INTERNATIONAL and (
            eligibility[b.venue].jurisdiction_status == "close_only"
        ):
            shadow_reasons.append("VENUE_CLOSE_ONLY_US_OPERATOR")
        reasons = list(dict.fromkeys(reasons))
        shadow_reasons = list(dict.fromkeys(shadow_reasons))
        execution = list(dict.fromkeys(execution))
        results.append(
            CandidateValidation(
                id=fingerprint([match.id, side, a.source])[:32],
                match_id=match.id,
                event_id=match.event_id,
                event=" vs ".join(a.participants),
                league=a.league,
                second_venue=b.venue,
                first_market_id=a.id,
                second_market_id=b.id,
                first_outcome=side,
                second_outcome=other,
                approval_status=match.status,
                human_reviewed=match.human_reviewed,
                at=instant,
                reasons=reasons,
                execution_reasons=execution,
                shadow_reasons=shadow_reasons,
                shadow_qualified=not shadow_reasons,
                executable_for_operator=not reasons and not execution,
                price_basis="Ask-depth profit is conditional on settlement equivalence.",
                first_ask=one.asks[0].price if one and one.asks else None,
                second_ask=two.asks[0].price if two and two.asks else None,
                available_quantity=available,
                first_book_age_ms=one.age_ms(instant) if one else None,
                second_book_age_ms=two.age_ms(instant) if two else None,
                calculation=calc,
                settlement_proof=proof,
                fee_evidence={
                    "first": a.fee.model_dump(mode="json"),
                    "second": b.fee.model_dump(mode="json"),
                    "additional": {
                        str(m.venue): risk.additional_costs.get(
                            m.venue, AdditionalCosts()
                        ).model_dump(mode="json")
                        for m in (a, b)
                    },
                },
                inputs={
                    "first_market": a.model_dump(mode="json"),
                    "second_market": b.model_dump(mode="json"),
                    "first_book": one.model_dump(mode="json") if one else None,
                    "second_book": two.model_dump(mode="json") if two else None,
                    "risk": risk.model_dump(mode="json"),
                },
                source=a.source,
                settlement_adjusted_net_profit=adjusted,
                known_scenario_net_floor=known_net,
            )
        )
    return results


async def validation_settings(store: Store) -> tuple[ValidationSettings, int, datetime]:
    async with store.sessions.begin() as session:
        row = await session.get(ValidationConfigRow, store.source)
        if row is None:
            row = ValidationConfigRow(
                id=store.source,
                source=store.source,
                revision=1,
                payload=ValidationSettings().model_dump(mode="json"),
            )
            session.add(row)
            await session.flush()
        return (
            ValidationSettings.model_validate(row.payload),
            row.revision,
            row.created_at.replace(tzinfo=UTC) if row.created_at.tzinfo is None else row.created_at,
        )


async def validation_tick(store: Store) -> None:
    settings, config_revision, _ = await validation_settings(store)
    if not settings.enabled:
        return
    markets = {m.id: m for m in await store.markets()}
    matches = await store.matches()
    books = await store.books()
    risk, _ = await store.risk()
    eligibility = await eligibility_status(store)
    families = await asyncio.to_thread(family_screen, matches, markets, eligibility)
    await persist_families(store, families)
    trades = await store.paper_trades()
    instant = now()
    current_ids = set()
    async with store.sessions.begin() as session:
        existing = {
            row.id: row
            for row in (
                await session.scalars(
                    select(ValidationRow).where(ValidationRow.source == store.source)
                )
            ).all()
        }
        active = {
            (row.match_id, Side(row.direction)): row
            for row in (
                await session.scalars(
                    select(EpisodeRow).where(
                        EpisodeRow.source == store.source, EpisodeRow.active.is_(True)
                    )
                )
            ).all()
        }
        for match in matches:
            a, b = markets.get(match.first_market_id), markets.get(match.second_market_id)
            if a is None or b is None:
                continue
            values = await asyncio.to_thread(
                diagnose,
                match,
                a,
                b,
                books,
                risk,
                eligibility,
                instant,
                exposure_for(trades, match.event_id, a.league, instant),
            )
            for value in values:
                current_ids.add(value.id)
                row = existing.get(value.id)
                if row:
                    row.payload = value.model_dump(mode="json")
                    row.updated_at = instant
                else:
                    session.add(
                        ValidationRow(
                            id=value.id,
                            source=store.source,
                            match_id=match.id,
                            direction=value.first_outcome,
                            payload=value.model_dump(mode="json"),
                        )
                    )
                key = (match.id, value.first_outcome)
                episode = active.get(key)
                if episode:
                    last = datetime.fromisoformat(episode.payload["last_seen"])
                    if (instant - last).total_seconds() > settings.episode_gap_seconds:
                        episode.active = False
                        episode.payload = {**episode.payload, "closed_at": instant.isoformat()}
                        episode = None
                if value.shadow_qualified:
                    if episode is None:
                        episode = EpisodeRow(
                            id=uuid4().hex,
                            source=store.source,
                            match_id=match.id,
                            direction=value.first_outcome,
                            active=True,
                            payload={
                                "started_at": instant.isoformat(),
                                "last_seen": instant.isoformat(),
                                "observations": 0,
                                "validation": value.model_dump(mode="json"),
                            },
                        )
                        session.add(episode)
                        active[key] = episode
                    episode.payload = {
                        **episode.payload,
                        "last_seen": instant.isoformat(),
                        "observations": episode.payload["observations"] + 1,
                        "validation": value.model_dump(mode="json"),
                    }
                elif episode:
                    episode.payload = {
                        **episode.payload,
                        "validation": value.model_dump(mode="json"),
                    }
        for identifier, row in existing.items():
            if identifier not in current_ids:
                await session.delete(row)
        for key, episode in active.items():
            if key[0] not in {match.id for match in matches}:
                episode.active = False
                episode.payload = {**episode.payload, "closed_at": instant.isoformat()}
        if settings.focus_automatic:
            size = 5 if not settings.focus_version else min(10, settings.shortlist_size)
            selected, scope = choose_focus(
                families,
                matches,
                markets,
                books,
                settings.selected_match_ids,
                size,
            )
            config_row = await session.get(ValidationConfigRow, store.source, with_for_update=True)
            if (
                config_row
                and config_row.revision == config_revision
                and (
                    selected != settings.selected_match_ids
                    or settings.focus_version != SCREEN_VERSION
                    or settings.focus_scope != scope
                )
            ):
                previous = config_row.payload
                config_row.payload = {
                    **settings.model_dump(mode="json"),
                    "selected_match_ids": selected,
                    "shortlist_size": size,
                    "focus_version": SCREEN_VERSION,
                    "focus_scope": scope,
                    "legacy_selected_match_ids": (
                        settings.selected_match_ids
                        if not settings.focus_version
                        else settings.legacy_selected_match_ids
                    ),
                }
                config_row.revision += 1
                audit(
                    session,
                    store.source,
                    "validation_review_shortlist",
                    "analysis",
                    {
                        "matches": selected,
                        "approval": "none",
                        "basis": "family_first",
                        "scope": scope,
                        "previous_matches": previous["selected_match_ids"],
                    },
                )


async def validation_report(store: Store) -> dict[str, Any]:
    settings, revision, started = await validation_settings(store)
    async with store.sessions() as session:
        rows = (
            await session.scalars(select(ValidationRow).where(ValidationRow.source == store.source))
        ).all()
        episodes = (
            await session.scalars(select(EpisodeRow).where(EpisodeRow.source == store.source))
        ).all()
        trials = (
            await session.scalars(select(ShadowRow).where(ShadowRow.source == store.source))
        ).all()
    values = [CandidateValidation.model_validate(row.payload) for row in rows]
    instant = now()
    reasons: dict[str, set[str]] = {}
    for value in values:
        for reason in {*value.reasons, *value.execution_reasons, *value.shadow_reasons}:
            reasons.setdefault(reason, set()).add(value.match_id)
    grouped: dict[str, list[CandidateValidation]] = {}
    for value in values:
        grouped.setdefault(value.match_id, []).append(value)
    shortlist = [
        grouped[identifier][0]
        for identifier in settings.selected_match_ids
        if identifier in grouped
    ]
    elapsed = max(0, (instant - started).total_seconds())
    baseline = [row for row in trials if row.payload["scenario"] == "baseline"]
    stresses = [row for row in trials if row.payload["scenario"] != "baseline"]
    baseline_profit = sum((D(row.payload.get("net_pnl", "0")) for row in baseline), D("0"))
    pnl_by_basis: dict[str, D] = {}
    lockup = D("0")
    weighted_lockup = D("0")
    for row in baseline:
        basis = row.payload.get("pnl_basis", "awaiting_execution")
        pnl_by_basis[basis] = pnl_by_basis.get(basis, D("0")) + D(row.payload.get("net_pnl", "0"))
        seconds = D(
            str(
                max(
                    0,
                    (
                        datetime.fromisoformat(row.payload.get("closed_at", instant.isoformat()))
                        - datetime.fromisoformat(row.payload["started_at"])
                    ).total_seconds(),
                )
            )
        )
        lockup += seconds
        weighted_lockup += seconds * D(row.payload["reserved_capital"])
    operating = settings.daily_operating_cost * D(str(elapsed)) / D("86400")
    fresh = [
        value
        for value in values
        if (instant - value.at).total_seconds() <= store.settings.validation_interval_seconds * 3
    ]
    return {
        "at": instant.isoformat(),
        "source": store.source,
        "revision": revision,
        "settings": settings.model_dump(mode="json"),
        "collection_started_at": started.isoformat(),
        "elapsed_days": str(D(str(elapsed)) / 86400),
        "diagnostic_window_complete": elapsed >= settings.diagnostic_days * 86400,
        "automatic_live_permission": False,
        "candidate_pairs": len(grouped),
        "directions": len(values),
        "fresh_diagnostics": len(fresh),
        "shadow_qualified_directions": sum(value.shadow_qualified for value in fresh),
        "operator_executable_directions": sum(value.executable_for_operator for value in fresh),
        "rejection_counts": {key: len(ids) for key, ids in sorted(reasons.items())},
        "approval_counts": dict(Counter(group[0].approval_status for group in grouped.values())),
        "shortlist": [value.model_dump(mode="json") for value in shortlist],
        "distinct_opportunity_episodes": len(episodes),
        "qualified_observations": sum(row.payload["observations"] for row in episodes),
        "shadow_baseline_trials": len(baseline),
        "shadow_stress_trials": len(stresses),
        "shadow_states": dict(Counter(row.state for row in baseline)),
        "simulated_baseline_net_pnl": str(baseline_profit),
        "baseline_pnl_by_basis": {key: str(value) for key, value in pnl_by_basis.items()},
        "stress_worst_case_pnl": str(
            sum((D(row.payload.get("net_pnl", "0")) for row in stresses), D("0"))
        ),
        "modeled_operating_cost": str(operating),
        "profit_after_operating_cost": (
            str(baseline_profit - operating) if settings.operating_cost_verified else None
        ),
        "operating_cost_status": "verified_assumption"
        if (settings.operating_cost_verified)
        else "unverified_not_deducted",
        "capital_lockup_seconds": str(lockup),
        "capital_weighted_lockup_usd_seconds": str(weighted_lockup),
        "eligibility": [
            item.model_dump(mode="json") for item in (await eligibility_status(store)).values()
        ],
        "limitation": (
            "Shadow results are hypothetical. Unknown account permissions, missing rules, "
            "fees or costs stay blocked. Seven to fourteen days is diagnostic, not live approval."
        ),
        "focused_validation": await focused_report(store),
    }

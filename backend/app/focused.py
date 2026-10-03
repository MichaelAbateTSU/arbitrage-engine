"""Persist a bounded family-first screen and conservative usable-observation time."""

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db import BookMonitorRow, CoverageRow, RuleFamilyRow
from app.domain import (
    AdditionalCosts,
    Book,
    D,
    Market,
    Match,
    RiskSettings,
    Side,
    Venue,
    fingerprint,
    now,
)
from app.eligibility import Eligibility, eligibility_status
from app.pricing import book_reasons, quantity
from app.settlement import SCREEN_VERSION, rule_profile, settlement_proof
from app.store import Store, upsert

STAGES = (
    "sampled",
    "settlement_approved",
    "usable_books",
    "approved_usable_books",
    "approved_priced_cost_verified",
    "fully_eligible_observed",
    "profitable_spread_observed",
    "eligible_without_qualifying_spread",
)


def venue_restrictions(
    a: Market,
    b: Market,
    eligibility: dict[Venue, Eligibility],
) -> list[str]:
    reasons = []
    for m in (a, b):
        status = eligibility[m.venue]
        if status.jurisdiction_status == "close_only":
            reasons.append("VENUE_CLOSE_ONLY_US_OPERATOR")
        if status.key_trading_scope == "restricted":
            reasons.append("API_KEY_HAS_NO_TRADING_SCOPE")
    return list(dict.fromkeys(reasons))


def instrument_mapping_issue(market: Market) -> str | None:
    return market.instrument_mapping_error()


def family_screen(
    matches: list[Match],
    markets: dict[str, Market],
    eligibility: dict[Venue, Eligibility],
    limit: int = 20,
) -> list[dict[str, Any]]:
    groups: dict[str, list[Match]] = {}
    for match in matches:
        a, b = markets.get(match.first_market_id), markets.get(match.second_market_id)
        if a is None or b is None:
            continue
        identifier = fingerprint([SCREEN_VERSION, [rule_profile(a), rule_profile(b)]])[:32]
        groups.setdefault(identifier, []).append(match)
    result = []
    ordered = sorted(
        groups.items(),
        key=lambda item: (
            markets[item[1][0].second_market_id].venue == Venue.INTERNATIONAL,
            item[0],
        ),
    )
    for identifier, values in ordered[:limit]:
        example = values[0]
        a, b = markets[example.first_market_id], markets[example.second_market_id]
        proof = settlement_proof(a, b)
        restrictions = venue_restrictions(a, b, eligibility)
        restricted = bool(restrictions)
        variable = any(
            profile["fallback"] == "independent_fair_price" for profile in proof["profiles"]
        )
        policy_disposition = (
            "nonconstant_hedge"
            if variable
            else "incompatible_profile"
            if {"OVERTIME_MISMATCH", "PERIOD_MISMATCH", "PAYOUT_MISMATCH"} & set(proof["reasons"])
            else "compatible_profile"
            if proof["proven"]
            else "unproven"
        )
        disposition = "restricted" if restricted else policy_disposition
        result.append(
            {
                "id": identifier,
                "current": True,
                "version": SCREEN_VERSION,
                "league": a.league,
                "second_venue": str(b.venue),
                "disposition": disposition,
                "settlement_disposition": policy_disposition,
                "restrictions": restrictions,
                "pair_count": len(values),
                "match_ids": [match.id for match in values],
                "representative_match_id": example.id,
                "profiles": proof["profiles"],
                "settlement_proof": proof,
                "contracts": [
                    {"id": m.id, "rules_hash": m.rules_hash, "rules_text": m.rules_text}
                    for m in (a, b)
                ],
                "reviewed_at": now().isoformat(),
                "reasons": (
                    restrictions
                    + (["INDEPENDENT_FAIR_PRICE_FLOOR_ZERO"] if variable else proof["reasons"])
                ),
                "review_scope": (
                    "Policy screen only; each pair still needs hash-bound independent review."
                ),
            }
        )
    reviewed = {value["id"] for value in result}
    inventory: dict[tuple[str, Venue, str], Market] = {}
    for market in sorted(markets.values(), key=lambda value: value.id):
        if market.tradable and market.status == "open":
            inventory.setdefault(
                (market.league, market.venue, fingerprint(rule_profile(market))), market
            )
    for (league, venue, _), a in inventory.items():
        if venue != Venue.KALSHI:
            continue
        for (other_league, other_venue, _), b in inventory.items():
            if league != other_league or other_venue == Venue.KALSHI:
                continue
            proof = settlement_proof(a, b)
            identifier = proof["family_id"]
            if identifier in reviewed or len(result) >= limit:
                continue
            restrictions = venue_restrictions(a, b, eligibility)
            restricted = bool(restrictions)
            variable = any(
                profile["fallback"] == "independent_fair_price" for profile in proof["profiles"]
            )
            result.append(
                {
                    "id": identifier,
                    "current": True,
                    "version": SCREEN_VERSION,
                    "league": league,
                    "second_venue": str(b.venue),
                    "disposition": (
                        "restricted"
                        if restricted
                        else "nonconstant_hedge"
                        if variable
                        else "unproven"
                    ),
                    "settlement_disposition": "nonconstant_hedge" if variable else "unproven",
                    "restrictions": restrictions,
                    "pair_count": 0,
                    "match_ids": [],
                    "representative_match_id": None,
                    "profiles": proof["profiles"],
                    "settlement_proof": proof,
                    "contracts": [
                        {"id": m.id, "rules_hash": m.rules_hash, "rules_text": m.rules_text}
                        for m in (a, b)
                    ],
                    "reviewed_at": now().isoformat(),
                    "reasons": (
                        restrictions
                        + (
                            ["INDEPENDENT_FAIR_PRICE_FLOOR_ZERO"]
                            if variable
                            else ["NO_MATCHED_PAIR"]
                        )
                    ),
                    "review_scope": (
                        "Inventory rule-family samples, not an event pair. "
                        "No matching, normal-winner coverage, or approval is implied."
                    ),
                }
            )
            reviewed.add(identifier)
    return result


def choose_focus(
    families: list[dict[str, Any]],
    matches: list[Match],
    markets: dict[str, Market],
    books: dict[tuple[str, Side], Book],
    previous: list[str],
    size: int,
) -> tuple[list[str], str]:
    promising = {
        identifier
        for family in families
        if family["disposition"] in ("compatible_profile", "unproven")
        for identifier in family["match_ids"]
    }
    diagnostic_only = not promising
    scope = (
        {
            identifier
            for family in families
            if "VENUE_CLOSE_ONLY_US_OPERATOR" not in family["restrictions"]
            for identifier in family["match_ids"]
        }
        if diagnostic_only
        else promising
    )
    groups: dict[str, list[Match]] = {}
    family_ids = {
        identifier: family["id"] for family in families for identifier in family["match_ids"]
    }
    for match in matches:
        if match.id not in scope:
            continue
        a, b = markets[match.first_market_id], markets[match.second_market_id]
        if not (a.tradable and b.tradable and a.status == b.status == "open"):
            continue
        groups.setdefault(family_ids[match.id], []).append(match)
    for values in groups.values():
        values.sort(
            key=lambda match: (
                match.id not in previous,
                -sum(
                    (
                        quantity(book.asks)
                        for (identifier, _), book in books.items()
                        if identifier in (match.first_market_id, match.second_market_id)
                    ),
                    D("0"),
                ),
                match.id,
            )
        )
    selected: list[str] = []
    events: set[str] = set()
    available = {match.id: match for values in groups.values() for match in values}
    for identifier in previous:
        retained = available.get(identifier)
        if retained is not None and retained.event_id not in events and len(selected) < size:
            selected.append(identifier)
            events.add(retained.event_id)
    while any(groups.values()) and len(selected) < size:
        for identifier in sorted(groups):
            values = groups[identifier]
            while values:
                candidate = values.pop(0)
                if candidate.event_id not in events:
                    selected.append(candidate.id)
                    events.add(candidate.event_id)
                    break
            if len(selected) >= size:
                break
    return selected, "diagnostic_only" if diagnostic_only else "potentially_eligible_review"


async def persist_families(store: Store, families: list[dict[str, Any]]) -> None:
    async with store.sessions.begin() as session:
        current_ids = {f"{store.source}:{value['id']}" for value in families}
        rows = (
            await session.scalars(select(RuleFamilyRow).where(RuleFamilyRow.source == store.source))
        ).all()
        for row in rows:
            if row.id not in current_ids and row.payload.get("current"):
                row.payload = {**row.payload, "current": False}
        for value in families:
            await upsert(
                session,
                RuleFamilyRow,
                f"{store.source}:{value['id']}",
                source=store.source,
                payload=value,
            )


async def monitor_update(store: Store, market_id: str, **values: Any) -> None:
    await monitor_updates(store, {market_id: values})


async def monitor_updates(store: Store, updates: dict[str, dict[str, Any]]) -> None:
    if not updates:
        return
    async with store.sessions.begin() as session:
        rows = (
            await session.scalars(
                select(BookMonitorRow)
                .where(
                    BookMonitorRow.source == store.source,
                    BookMonitorRow.market_id.in_(list(updates)),
                )
                .with_for_update()
            )
        ).all()
        previous = {row.market_id: row.payload for row in rows}
        captured = now()
        values = [
            {
                "id": fingerprint([store.source, identifier])[:32],
                "source": store.source,
                "market_id": identifier,
                "payload": {
                    **previous.get(identifier, {}),
                    **update,
                    "updated_at": captured.isoformat(),
                },
            }
            for identifier, update in updates.items()
        ]
        insert = sqlite_insert if session.get_bind().dialect.name == "sqlite" else pg_insert
        statement = insert(BookMonitorRow).values(values)
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=["id"],
                set_={"payload": statement.excluded.payload, "updated_at": captured},
            )
        )


def book_diagnostic(
    market: Market,
    side: Side,
    book: Book | None,
    monitor: dict[str, Any],
    risk: RiskSettings,
    instant: datetime,
) -> dict[str, Any]:
    active = (
        bool(monitor.get("selection_at"))
        and 0 <= (instant - datetime.fromisoformat(monitor["selection_at"])).total_seconds() < 900
    )
    if not market.tradable or market.status != "open":
        cause = "MARKET_CLOSED_OR_PAUSED"
    elif active and monitor.get("selection") == "excluded_by_cap":
        cause = "EXCLUDED_BY_MONITORING_CAP"
    elif active and monitor.get("subscription") == "rejected":
        cause = "SUBSCRIPTION_REJECTED"
    elif active and monitor.get("mapping") == "invalid":
        cause = "INSTRUMENT_MAPPING_INVALID"
    elif active and monitor.get("market_state") == "not_open":
        cause = "MARKET_CLOSED_OR_PAUSED"
    elif active and monitor.get("stream_error") not in (
        None,
        "PERIODIC_AUTHORITATIVE_RESNAPSHOT",
    ):
        cause = f"FEED_FAILURE_{monitor['stream_error']}"
    elif book is None:
        cause = (
            "REST_BOOK_AVAILABLE_BUT_STREAM_SNAPSHOT_MISSING"
            if monitor.get("rest_probe", {}).get("status") == "snapshot_received"
            else "STREAM_DATA_RECEIVED_BUT_NO_PERSISTED_BOOK"
            if active and monitor.get("subscription") == "confirmed_by_data"
            else "AWAITING_AUTHORITATIVE_SNAPSHOT"
            if active and monitor.get("subscription") in ("requested", "confirmed")
            else "NEVER_REQUESTED_IN_CURRENT_GENERATION"
            if active and monitor.get("subscription") == "not_requested"
            else "MONITORING_EVIDENCE_UNAVAILABLE"
        )
    elif not book.connected or not book.synchronized:
        cause = "DISCONNECTED_OR_INVALID_RECONSTRUCTION"
    elif book.age_ms(instant) > risk.max_quote_age_ms:
        cause = "STALE_SNAPSHOT"
    elif not book.asks:
        cause = "EMPTY_PURCHASE_LIQUIDITY"
    else:
        failures = book_reasons(book, market, instant, risk.max_quote_age_ms)
        cause = failures[0] if failures else "USABLE_BOOK"
    return {
        "market_id": market.id,
        "instrument": market.external_id,
        "outcome": str(side),
        "cause": cause,
        "age_ms": book.age_ms(instant) if book else None,
        "asks": len(book.asks) if book else None,
        "bids": len(book.bids) if book else None,
        "monitoring": monitor,
        "monitoring_evidence_current": active,
    }


def quote_expiry(book: Book, risk: RiskSettings) -> datetime:
    timestamps = [book.received_at, book.exchange_at, book.requested_at]
    return min(value for value in timestamps if value is not None) + timedelta(
        milliseconds=risk.max_quote_age_ms
    )


def coverage_frame(
    match: Match,
    a: Market,
    b: Market,
    books: dict[tuple[str, Side], Book],
    risk: RiskSettings,
    eligibility: dict[Venue, Eligibility],
    instant: datetime,
    monitoring: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    proof = settlement_proof(a, b)
    approved = (
        match.status == "approved"
        and proof["proven"]
        and [match.first_rules_hash, match.second_rules_hash] == [a.rules_hash, b.rules_hash]
        and (match.human_reviewed or a.source == "demo")
    )
    usable = True
    if a.source == "public":
        usable = bool(monitoring) and all(
            monitoring is not None
            and monitoring.get(m.id, {}).get("generation")
            and monitoring.get(m.id, {}).get("selection") == "selected"
            and m.instrument_mapping_error() is None
            for m in (a, b)
        )
    book_expiries = [instant + timedelta(seconds=2)]
    financial_expiries = [instant + timedelta(seconds=2)]
    for side in Side:
        other = side if match.inverted else side.opposite
        one, two = books.get((a.id, side)), books.get((b.id, other))
        if one is None or two is None:
            usable = False
            continue
        if (
            not one.asks
            or not two.asks
            or book_reasons(one, a, instant, risk.max_quote_age_ms)
            or book_reasons(two, b, instant, risk.max_quote_age_ms)
            or abs((one.received_at - two.received_at).total_seconds() * 1000)
            > risk.max_book_skew_ms
        ):
            usable = False
        book_expiries += [quote_expiry(one, risk), quote_expiry(two, risk)]
    known_costs = all(
        risk.additional_costs.get(m.venue, AdditionalCosts()).known_at(instant) for m in (a, b)
    )
    known_fees = all(m.fee.known_at(instant) for m in (a, b))
    for m in (a, b):
        if m.fee.valid_until:
            financial_expiries.append(m.fee.valid_until)
        for component in risk.additional_costs.get(m.venue, AdditionalCosts()).components.values():
            if component.expires_at:
                financial_expiries.append(component.expires_at)
        e = eligibility[m.venue]
        if e.balance_observed_at:
            financial_expiries.append(e.balance_observed_at + timedelta(seconds=900))
        if e.attestation:
            financial_expiries.append(datetime.fromisoformat(e.attestation["expires_at"]))
    cost_ready = (
        approved
        and usable
        and known_costs
        and known_fees
        and (a.quote_currency == b.quote_currency or risk.allow_usdc_parity_assumption)
    )
    eligible = all(
        eligibility[m.venue].open_order_eligible
        and m.venue not in risk.venue_kill_switches
        and m.id not in risk.blocked_markets
        and m.league in risk.supported_leagues
        for m in (a, b)
    )
    funded = False
    profitable_directions = []
    if cost_ready and eligible:
        from app.arbitrage import calculate

        balances = [eligibility[m.venue].available_balance for m in (a, b)]
        if all(value is not None and value > 0 for value in balances):
            cap = min([risk.max_per_venue, *[value for value in balances if value is not None]])
            sized_risk = risk.model_copy(update={"max_per_venue": cap})
            for side in Side:
                other = side if match.inverted else side.opposite
                result = calculate(books[(a.id, side)], books[(b.id, other)], a, b, sized_risk)
                if result is not None:
                    funded = True
                    if (
                        result.net_profit > 0
                        and result.net_profit >= risk.min_profit
                        and result.net_return >= risk.min_edge
                    ):
                        profitable_directions.append(str(side))
    return {
        "at": instant.isoformat(),
        "valid_until": (instant + timedelta(seconds=2)).isoformat(),
        "book_valid_until": min(book_expiries).isoformat(),
        "financial_valid_until": min(financial_expiries).isoformat(),
        "profitable_directions": profitable_directions,
        "evidence_key": fingerprint(
            [
                match.model_dump(mode="json"),
                risk.model_dump(mode="json"),
                [m.fee.model_dump(mode="json") for m in (a, b)],
                [eligibility[m.venue].attestation for m in (a, b)],
                [
                    {
                        "balance": eligibility[m.venue].available_balance,
                        "balance_at": eligibility[m.venue].balance_observed_at,
                        "key_scope_at": eligibility[m.venue].key_scope_observed_at,
                        "key_scope": eligibility[m.venue].key_trading_scope,
                        "key_binding": eligibility[m.venue].key_binding_status,
                        "order_permission": eligibility[m.venue].order_permission,
                        "jurisdiction": eligibility[m.venue].jurisdiction_status,
                    }
                    for m in (a, b)
                ],
                [
                    [
                        monitoring.get(m.id, {}).get("generation"),
                        monitoring.get(m.id, {}).get("integrity_epoch", 0),
                    ]
                    for m in (a, b)
                ]
                if monitoring is not None
                else None,
            ]
        ),
        "stages": {
            "sampled": True,
            "settlement_approved": approved,
            "usable_books": usable,
            "approved_usable_books": approved and usable,
            "approved_priced_cost_verified": cost_ready,
            "fully_eligible_observed": cost_ready and eligible and funded,
            "profitable_spread_observed": cost_ready and eligible and bool(profitable_directions),
            "eligible_without_qualifying_spread": cost_ready
            and eligible
            and funded
            and not profitable_directions,
        },
    }


def credit_interval(previous: dict[str, Any], current: dict[str, Any]) -> tuple[datetime, datetime]:
    start = datetime.fromisoformat(previous["at"])
    end = min(
        datetime.fromisoformat(current["at"]),
        start + timedelta(seconds=2),
        datetime.fromisoformat(previous["valid_until"]),
    )
    if (
        previous["evidence_key"] != current["evidence_key"]
        or (datetime.fromisoformat(current["at"]) - start).total_seconds() > 2
        or end < start
    ):
        end = start
    return start, end


async def coverage_tick(store: Store) -> None:
    from app.validation import validation_settings

    config, _, _ = await validation_settings(store)
    if not config.enabled or not config.selected_match_ids:
        return
    matches = await store.matches(config.selected_match_ids)
    ids = [identifier for m in matches for identifier in (m.first_market_id, m.second_market_id)]
    markets = {m.id: m for m in await store.markets(ids)}
    books = await store.books(ids)
    risk, _ = await store.risk()
    eligibility = await eligibility_status(store)
    instant = now()
    async with store.sessions.begin() as session:
        monitors = (
            await session.scalars(
                select(BookMonitorRow).where(
                    BookMonitorRow.source == store.source,
                    BookMonitorRow.market_id.in_(ids),
                )
            )
        ).all()
        monitoring = {row.market_id: row.payload for row in monitors}
        rows = (
            await session.scalars(
                select(CoverageRow).where(
                    CoverageRow.source == store.source,
                    CoverageRow.match_id.in_([m.id for m in matches]),
                    CoverageRow.day >= (instant - timedelta(days=1)).date().isoformat(),
                )
            )
        ).all()
        previous_rows: dict[str, CoverageRow] = {}
        daily = {(row.match_id, row.day): row for row in rows}
        for historical_row in rows:
            old = previous_rows.get(historical_row.match_id)
            if old is None or (historical_row.payload["last"]["at"], historical_row.day) > (
                old.payload["last"]["at"],
                old.day,
            ):
                previous_rows[historical_row.match_id] = historical_row
        for match in matches:
            if match.first_market_id not in markets or match.second_market_id not in markets:
                continue
            current = coverage_frame(
                match,
                markets[match.first_market_id],
                markets[match.second_market_id],
                books,
                risk,
                eligibility,
                instant,
                monitoring,
            )
            previous_row = previous_rows.get(match.id)
            previous = previous_row.payload["last"] if previous_row else current
            start, end = credit_interval(previous, current)
            while start < end:
                midnight = datetime.combine(
                    start.date() + timedelta(days=1), datetime.min.time(), UTC
                )
                stop = min(end, midnight)
                day = start.date().isoformat()
                row: CoverageRow | None = daily.get((match.id, day))
                if row is None:
                    row = CoverageRow(
                        id=fingerprint([store.source, match.id, day])[:32],
                        source=store.source,
                        match_id=match.id,
                        day=day,
                        payload={"seconds": {name: "0" for name in STAGES}, "last": current},
                    )
                    session.add(row)
                    daily[(match.id, day)] = row
                totals = dict(row.payload["seconds"])
                for name in STAGES:
                    if previous["stages"].get(name, False) and current["stages"][name]:
                        stage_stop = stop
                        if name in (
                            "usable_books",
                            "approved_usable_books",
                            "approved_priced_cost_verified",
                            "fully_eligible_observed",
                            "profitable_spread_observed",
                            "eligible_without_qualifying_spread",
                        ):
                            stage_stop = min(
                                stage_stop, datetime.fromisoformat(previous["book_valid_until"])
                            )
                        if name in (
                            "approved_priced_cost_verified",
                            "fully_eligible_observed",
                            "profitable_spread_observed",
                            "eligible_without_qualifying_spread",
                        ):
                            stage_stop = min(
                                stage_stop,
                                datetime.fromisoformat(previous["financial_valid_until"]),
                            )
                        seconds = D(str(max(0, (stage_stop - start).total_seconds())))
                        totals[name] = str(D(totals.get(name, "0")) + seconds)
                row.payload = {**row.payload, "seconds": totals, "last": current}
                start = stop
            day = instant.date().isoformat()
            row = daily.get((match.id, day))
            if row is None:
                row = CoverageRow(
                    id=fingerprint([store.source, match.id, day])[:32],
                    source=store.source,
                    match_id=match.id,
                    day=day,
                    payload={"seconds": {name: "0" for name in STAGES}, "last": current},
                )
                session.add(row)
            else:
                row.payload = {**row.payload, "last": current}
            last_seen = (
                dict(previous_row.payload.get("profit_last_seen", {})) if previous_row else {}
            )
            windows = row.payload.get("profitable_windows", 0)
            for side in current["profitable_directions"]:
                seen = last_seen.get(side)
                if (
                    seen is None
                    or (instant - datetime.fromisoformat(seen)).total_seconds()
                    > config.episode_gap_seconds
                ):
                    windows += 1
                last_seen[side] = instant.isoformat()
            row.payload = {
                **row.payload,
                "profitable_windows": windows,
                "profit_last_seen": last_seen,
            }


async def focused_report(store: Store) -> dict[str, Any]:
    from app.validation import validation_settings

    config, _, _ = await validation_settings(store)
    async with store.sessions() as session:
        families = (
            await session.scalars(select(RuleFamilyRow).where(RuleFamilyRow.source == store.source))
        ).all()
        monitors = (
            await session.scalars(
                select(BookMonitorRow).where(BookMonitorRow.source == store.source)
            )
        ).all()
        coverage = (
            await session.scalars(select(CoverageRow).where(CoverageRow.source == store.source))
        ).all()
    monitor_map = {row.market_id: row.payload for row in monitors}
    matches = await store.matches(config.selected_match_ids)
    ids = [identifier for m in matches for identifier in (m.first_market_id, m.second_market_id)]
    markets = {m.id: m for m in await store.markets(ids)}
    books = await store.books(ids)
    risk, _ = await store.risk()
    instant = now()
    focused = []
    for match in matches:
        a, b = markets.get(match.first_market_id), markets.get(match.second_market_id)
        if a is None or b is None:
            continue
        focused.append(
            {
                "match_id": match.id,
                "event": " vs ".join(a.participants),
                "league": a.league,
                "scope": config.focus_scope,
                "family_id": settlement_proof(a, b)["family_id"],
                "books": [
                    book_diagnostic(
                        m, side, books.get((m.id, side)), monitor_map.get(m.id, {}), risk, instant
                    )
                    for m in (a, b)
                    for side in Side
                ],
            }
        )
    totals = {
        name: str(sum((D(row.payload["seconds"].get(name, "0")) for row in coverage), D("0")))
        for name in STAGES
    }
    current_families = [row.payload for row in families if row.payload.get("current")]
    return {
        "families": current_families,
        "family_counts": dict(Counter(value["disposition"] for value in current_families)),
        "family_review_limit": 20,
        "focused_pairs": focused,
        "focus_scope": config.focus_scope,
        "coverage": {
            "tracking_started_at": min(
                (
                    (
                        row.created_at.replace(tzinfo=UTC)
                        if row.created_at.tzinfo is None
                        else row.created_at
                    ).isoformat()
                    for row in coverage
                ),
                default=None,
            ),
            "pair_seconds": totals,
            "pair_count": len({row.match_id for row in coverage}),
            "distinct_funded_spread_windows": sum(
                row.payload.get("profitable_windows", 0) for row in coverage
            ),
            "verdict": (
                "inconclusive_no_fully_evidenced_coverage"
                if D(totals["fully_eligible_observed"]) == 0
                else "observed_eligible_pair_time"
            ),
            "historical_coverage_before_tracking": "unknown_not_reconstructed",
            "method": (
                "Lower-bound pair-seconds: adjacent <=2s samples, unchanged evidence, "
                "clipped to earlier quote/fee/cost/permission expiry. Not wall-clock days "
                "or proof of continuous execution profitability."
            ),
            "by_pair": [
                {"match_id": row.match_id, "day": row.day, "seconds": row.payload["seconds"]}
                for row in coverage
            ],
        },
        "cost_evidence": {
            str(venue): {
                "complete": risk.additional_costs.get(venue, AdditionalCosts()).known_at(instant),
                "unknown_components": risk.additional_costs.get(
                    venue, AdditionalCosts()
                ).unknown_components(instant),
                "assumptions": risk.additional_costs.get(venue, AdditionalCosts()).model_dump(
                    mode="json"
                ),
            }
            for venue in Venue
        },
        "screen_conclusion": (
            "Family screening has not run yet; no compatibility conclusion is available."
            if not current_families
            else "No compatible profile found in the bounded reviewed scope. "
            "Diagnostic sample is not an executable universe."
            if not any(
                f.get("settlement_disposition", f["disposition"]) == "compatible_profile"
                for f in current_families
            )
            else "Compatible profiles still require individual settlement and eligibility evidence."
        ),
    }

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import select
from test_validation import profiles

from app.arbitrage import calculate, detect
from app.db import CoverageRow, EligibilityRow, ShadowRow, ValidationConfigRow
from app.domain import (
    COST_COMPONENTS,
    AdditionalCosts,
    CostComponent,
    D,
    Exposure,
    Side,
    Venue,
    now,
)
from app.eligibility import eligibility_status, refresh_kalshi_scope
from app.focused import (
    book_diagnostic,
    choose_focus,
    coverage_frame,
    coverage_tick,
    credit_interval,
    family_screen,
    focused_report,
    monitor_updates,
    quote_expiry,
)
from app.matching import extract_rules, match_markets
from app.settlement import settlement_proof
from app.shadow import complete_trial, shadow_tick, unwind_trial
from app.validation import diagnose, validation_settings, validation_tick
from app.workers import focused_signature, guarded_loop


def evidenced_costs(amount="0"):
    return AdditionalCosts(
        components={
            name: CostComponent(
                status="verified_amount"
                if name == "settlement" and D(amount) > 0
                else "verified_zero",
                amount=D(amount) if name == "settlement" else D("0"),
                basis="per_contract",
                execution_path="Synthetic USD funds already on venue; allocated once per contract",
                evidence="Synthetic test-only cost evidence, not a venue fee promise",
                expires_at=now() + timedelta(days=1),
            )
            for name in COST_COMPONENTS
        }
    )


def test_family_profile_separates_calendar_days_from_48_hours(scenario):
    a, b, _, _, _ = scenario
    a.rules_text = (
        "If postponed but begins within 48 hours, use final result. "
        "If not started within 48 hours, market will resolve to a fair price."
    )
    b.rules_text = (
        "Overtime is included if played. If the game ends in a tie, "
        "the market will settle to $0.50. If postponed and not rescheduled "
        "to start within two calendar days, the market will settle to the last fair market price. "
        "Outcome sourced from NFL."
    )
    parsed = extract_rules(b.rules_text)
    assert parsed.overtime == "included"
    assert parsed.draw == "half_refund"
    assert parsed.postponement == "within_two_calendar_days"
    assert parsed.settlement_source == "nfl"
    assert parsed.cancellation is None
    proof = settlement_proof(a, b)
    assert not proof["proven"]
    assert proof["profiles"][0]["postponement"] == "within_48h"
    assert proof["profiles"][1]["postponement"] == "within_two_calendar_days"
    assert proof["direction_bounds"]["YES"]["known_scenario_floor"] == "0"
    assert any(row["scenario"] == "discretionary_settlement" for row in proof["scenarios"])


def test_postponement_is_not_cancellation_or_an_automatic_approval(scenario):
    a, b, books, _, risk = scenario
    a.rules_text += " If postponed beyond 48 hours, the market will resolve to a fair price."
    proof = settlement_proof(a, b)
    assert not proof["proven"]
    assert any(row["minimum_payout"] == "0" for row in proof["scenarios"])
    match = match_markets(a, b, human_reviewed=True)
    signals = diagnose(match, a, b, books, risk, profiles(), now(), Exposure())
    assert not any(value.shadow_qualified for value in signals)
    assert signals[0].known_scenario_net_floor < 0
    assert not any(
        value.risk_status == "qualified" for value in detect(match, a, b, books, risk, now())[0]
    )


def test_unknown_discretionary_terms_keep_public_proof_unproven(scenario):
    a, b, _, _, _ = scenario
    a.source = b.source = "public"
    proof = settlement_proof(a, b)
    assert proof["direction_bounds"]["YES"]["minimum_combined_payout"] is None
    assert not proof["proven"]
    for market in (a, b):
        market.rules.evidence["discretionary_settlement"] = (
            "Test independent full-governing-terms review"
        )
        market.rules.discretionary_settlement = "excluded"
    assert settlement_proof(a, b)["proven"]
    b.rules_text += " Discretionary market settlement to the last fair market price."
    assert not settlement_proof(a, b)["proven"]
    assert "DISCRETIONARY_POLICY_CONTRADICTS_SOURCE" in match_markets(a, b, True).reasons


def test_conditional_winner_rows_do_not_create_an_all_scenario_profit_floor(scenario):
    a, b, books, _, risk = scenario
    b.rules.overtime = "excluded"
    proof = settlement_proof(a, b)
    assert proof["direction_bounds"]["YES"]["minimum_combined_payout"] is None
    match = match_markets(a, b, human_reviewed=True)
    signal = diagnose(match, a, b, books, risk, profiles(), now(), Exposure())[0]
    assert signal.calculation is not None
    assert signal.settlement_adjusted_net_profit is None
    assert not signal.shadow_qualified


@pytest.mark.parametrize(
    "status,amount",
    [
        ("verified_amount", "0"),
        ("verified_zero", "1"),
        ("not_applicable", "1"),
        ("unknown", "1"),
        ("verified_amount", "NaN"),
        ("verified_zero", False),
    ],
)
def test_cost_status_cannot_conceal_a_charge_or_nonfinite_money(status, amount):
    with pytest.raises(ValidationError):
        CostComponent(
            status=status,
            amount=amount,
            execution_path="test",
            evidence="test",
            expires_at=now() + timedelta(days=1),
        )


def test_component_costs_are_exact_expiring_and_do_not_double_count(scenario):
    a, b, books, _, risk = scenario
    legacy = AdditionalCosts(verified=True, evidence="Old aggregate attestation")
    assert legacy.unknown_components(now()) == list(COST_COMPONENTS)
    costs = evidenced_costs("0.01234567")
    assert costs.known_at(now())
    assert costs.total(D("1.23456789")) == D("0.0152415677625363")
    assert not costs.known_at(now() + timedelta(days=2))
    with pytest.raises(ValidationError):
        AdditionalCosts(fixed_per_leg="1", components=costs.components)
    with pytest.raises(ValidationError):
        CostComponent(status="verified_zero", evidence="URL", execution_path="USD")
    risk.additional_costs = {a.venue: costs}
    calc = calculate(books[(a.id, Side.YES)], books[(b.id, Side.NO)], a, b, risk)
    assert calc.additional_cost_one == calc.quantity * D("0.01234567")
    assert calc.cost_one + calc.fee_one + calc.additional_cost_one <= risk.max_per_venue


def test_book_causes_do_not_infer_empty_books_or_successful_subscriptions(scenario):
    a, _, books, _, risk = scenario
    at = now()
    selected = {
        "selection_at": at.isoformat(),
        "selection": "selected",
        "subscription": "requested",
    }
    assert (
        book_diagnostic(a, Side.YES, None, {}, risk, at)["cause"]
        == "MONITORING_EVIDENCE_UNAVAILABLE"
    )
    assert (
        book_diagnostic(a, Side.YES, None, selected, risk, at)["cause"]
        == "AWAITING_AUTHORITATIVE_SNAPSHOT"
    )
    assert (
        book_diagnostic(
            a,
            Side.YES,
            None,
            {
                **selected,
                "selection": "excluded_by_cap",
            },
            risk,
            at,
        )["cause"]
        == "EXCLUDED_BY_MONITORING_CAP"
    )
    assert (
        book_diagnostic(
            a,
            Side.YES,
            None,
            {
                **selected,
                "subscription": "rejected",
            },
            risk,
            at,
        )["cause"]
        == "SUBSCRIPTION_REJECTED"
    )
    assert (
        book_diagnostic(
            a,
            Side.YES,
            None,
            {
                **selected,
                "mapping": "invalid",
            },
            risk,
            at,
        )["cause"]
        == "INSTRUMENT_MAPPING_INVALID"
    )
    book = books[(a.id, Side.YES)]
    book.asks = []
    assert (
        book_diagnostic(a, Side.YES, book, selected, risk, at)["cause"]
        == "EMPTY_PURCHASE_LIQUIDITY"
    )
    book.received_at -= timedelta(minutes=1)
    assert book_diagnostic(a, Side.YES, book, selected, risk, at)["cause"] == "STALE_SNAPSHOT"
    book.synchronized = False
    assert (
        book_diagnostic(a, Side.YES, book, selected, risk, at)["cause"]
        == "DISCONNECTED_OR_INVALID_RECONSTRUCTION"
    )


async def test_focused_scope_is_bounded_and_preserves_original_clock_and_old_selection(store):
    _, _, started = await validation_settings(store)
    old_ids = [match.id for match in await store.matches()]
    async with store.sessions.begin() as session:
        row = await session.get(ValidationConfigRow, store.source)
        row.payload = {**row.payload, "selected_match_ids": old_ids, "shortlist_size": 20}
    await validation_tick(store)
    config, _, after = await validation_settings(store)
    assert after == started
    assert config.legacy_selected_match_ids == old_ids
    assert len(config.selected_match_ids) <= 5
    assert config.shortlist_size == 5
    report = await focused_report(store)
    assert len(report["families"]) <= 20
    assert not any(
        market["instrument"].startswith("wrong")
        for pair in report["focused_pairs"]
        for market in pair["books"]
    )
    assert report["coverage"]["verdict"].startswith("inconclusive")


async def test_monitoring_writes_merge_status_and_probe_evidence(store):
    await validation_tick(store)
    first_pair = (await focused_report(store))["focused_pairs"][0]
    market = next(m for m in await store.markets() if m.id == first_pair["books"][0]["market_id"])
    await monitor_updates(
        store, {market.id: {"selection": "selected", "selection_at": now().isoformat()}}
    )
    await monitor_updates(
        store, {market.id: {"rest_probe": {"status": "failed", "error_code": "HTTP_404"}}}
    )
    await validation_tick(store)
    report = await focused_report(store)
    rows = [
        book
        for pair in report["focused_pairs"]
        for book in pair["books"]
        if book["market_id"] == market.id
    ]
    assert rows
    assert rows[0]["monitoring"]["selection"] == "selected"
    assert rows[0]["monitoring"]["rest_probe"]["error_code"] == "HTTP_404"


async def test_unwind_includes_component_allocations_without_double_charging_settlement(store):
    await validation_tick(store)
    await shadow_tick(store)
    async with store.sessions() as session:
        row = next(
            row
            for row in (await session.scalars(select(ShadowRow))).all()
            if row.payload["scenario"] == "second_leg_rejected"
        )
    payload = row.payload
    markets = {m.id: m for m in await store.markets()}
    signal = payload["validation"]
    a, b = markets[signal["first_market_id"]], markets[signal["second_market_id"]]
    costs = evidenced_costs()
    component = costs.components["rebalancing"].model_copy(
        update={
            "status": "verified_amount",
            "amount": D("0.40"),
            "basis": "per_leg",
            "applies_to": "both",
        }
    )
    costs = costs.model_copy(update={"components": {**costs.components, "rebalancing": component}})
    payload["risk"] = {
        **payload["risk"],
        "additional_costs": {str(a.venue): costs.model_dump(mode="json")},
    }
    books = await store.books()
    at = now() + timedelta(seconds=1)
    for book in books.values():
        book.received_at = book.exchange_at = at
    state, filled = complete_trial(payload, a, b, books, at)
    assert state == "UNWIND_PENDING"
    at += timedelta(seconds=1)
    for book in books.values():
        book.received_at = book.exchange_at = at
    _, closed = unwind_trial(filled, a, b, books, at)
    assert closed["unwind_additional_cost"] == "0.40"
    assert D(closed["net_pnl"]) == (
        D(closed["unwind_proceeds"])
        - D(closed["spent"])
        - D(closed["unwind_fee"])
        - D("0.40")
        - D(closed["execution_slippage"])
    )


def test_coverage_proves_a_decision_without_demanding_profit_but_requires_capital(scenario):
    a, b, books, match, risk = scenario
    risk.additional_costs = {m.venue: evidenced_costs() for m in (a, b)}
    eligibility = profiles()
    for value in eligibility.values():
        value.open_order_eligible = True
        value.available_balance = D("500")
    at = now()
    frame = coverage_frame(match, a, b, books, risk, eligibility, at)
    assert frame["stages"]["fully_eligible_observed"]
    assert frame["stages"]["profitable_spread_observed"]
    risk.min_profit = D("1000000")
    negative = coverage_frame(match, a, b, books, risk, eligibility, at)
    assert negative["stages"]["fully_eligible_observed"]
    assert negative["stages"]["eligible_without_qualifying_spread"]
    risk.min_profit = D("1")
    eligibility[a.venue].available_balance = D("0")
    assert not coverage_frame(match, a, b, books, risk, eligibility, at)["stages"][
        "fully_eligible_observed"
    ]
    book = books[(a.id, Side.YES)]
    book.requested_at = at - timedelta(milliseconds=2900)
    assert quote_expiry(book, risk) == at + timedelta(milliseconds=100)
    previous = coverage_frame(match, a, b, books, risk, eligibility, at)
    assert datetime.fromisoformat(previous["book_valid_until"]) == at + timedelta(milliseconds=100)
    current = {**previous, "at": (at + timedelta(seconds=1)).isoformat()}
    start, end = credit_interval(previous, current)
    assert (end - start).total_seconds() == 1
    assert credit_interval(previous, {**current, "evidence_key": "changed"}) == (start, start)
    assert credit_interval(
        previous, {**current, "at": (at + timedelta(seconds=20)).isoformat()}
    ) == (start, start)


async def test_coverage_survives_restart_splits_midnight_and_credits_no_worker_gap(
    store, monkeypatch
):
    await validation_tick(store)
    at = datetime(2026, 10, 3, 23, 59, 59, 700000, tzinfo=UTC)
    monkeypatch.setattr("app.focused.now", lambda: at)
    await coverage_tick(store)
    at += timedelta(milliseconds=500)
    await coverage_tick(store)
    async with store.sessions() as session:
        rows = (await session.scalars(select(CoverageRow))).all()
        first = sum((D(row.payload["seconds"]["sampled"]) for row in rows), D("0"))
        assert first > 0
        assert {row.day for row in rows} == {"2026-10-03", "2026-10-04"}
    at += timedelta(minutes=1)
    await coverage_tick(store)
    async with store.sessions() as session:
        rows = (await session.scalars(select(CoverageRow))).all()
        assert sum((D(row.payload["seconds"]["sampled"]) for row in rows), D("0")) == first
        assert all(D(row.payload["seconds"]["usable_books"]) == 0 for row in rows)
    report = await focused_report(store)
    assert report["coverage"]["tracking_started_at"] is not None
    assert report["coverage"]["verdict"].startswith("inconclusive")


async def test_funded_spread_windows_deduplicate_across_midnight(store, monkeypatch):
    from app.focused import STAGES

    await validation_tick(store)
    selected = (await validation_settings(store))[0].selected_match_ids
    at = datetime(2026, 10, 3, 23, 59, 59, 700000, tzinfo=UTC)
    monkeypatch.setattr("app.focused.now", lambda: at)

    def frame(*args):
        return {
            "at": at.isoformat(),
            "valid_until": (at + timedelta(seconds=2)).isoformat(),
            "book_valid_until": (at + timedelta(seconds=2)).isoformat(),
            "financial_valid_until": (at + timedelta(seconds=2)).isoformat(),
            "evidence_key": "synthetic-same-evidence",
            "profitable_directions": ["YES"],
            "stages": {name: name != "eligible_without_qualifying_spread" for name in STAGES},
        }

    monkeypatch.setattr("app.focused.coverage_frame", frame)
    for _ in range(4):
        await coverage_tick(store)
        at += timedelta(milliseconds=500)
    report = await focused_report(store)
    assert report["coverage"]["distinct_funded_spread_windows"] == len(selected)
    assert D(report["coverage"]["pair_seconds"]["fully_eligible_observed"]) > 0
    at += timedelta(seconds=65)
    await coverage_tick(store)
    report = await focused_report(store)
    assert report["coverage"]["distinct_funded_spread_windows"] == len(selected) * 2


async def test_read_only_key_scopes_are_separate_from_account_permission_and_secret_free(
    store, monkeypatch
):
    store.settings.kalshi_api_key = SecretStr("test-key-id-not-a-real-credential")
    monkeypatch.setattr("app.eligibility.kalshi_headers", lambda *args: {})
    calls = []

    def response(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(
            200,
            json={
                "api_keys": [
                    {"api_key_id": "test-key-id-not-a-real-credential", "scopes": ["read"]}
                ],
                "api_key_region_expiration_ts": int((now() - timedelta(days=1)).timestamp()),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        await refresh_kalshi_scope(store, client)
    assert calls == [("GET", "/trade-api/v2/api_keys")]
    status = (await eligibility_status(store))[Venue.KALSHI]
    assert status.key_trading_scope == "restricted"
    assert status.key_region_status == "expired"
    assert status.order_permission == "restricted"
    assert not status.open_order_eligible
    async with store.sessions() as session:
        row = await session.get(EligibilityRow, f"{store.source}:{Venue.KALSHI}")
        assert "test-key-id" not in str(row.payload)
        assert "scopes" not in str(row.payload)


def test_family_review_keeps_international_restricted_and_fair_price_unapproved(scenario):
    a, b, _, _, _ = scenario
    a.rules_text += " Market will resolve to a fair price if not started within 48 hours."
    match = match_markets(a, b)
    values = family_screen([match], {a.id: a, b.id: b}, profiles())
    assert values[0]["disposition"] == "nonconstant_hedge"
    b.venue = Venue.INTERNATIONAL
    eligibility = profiles()
    eligibility[b.venue].jurisdiction_status = "close_only"
    values = family_screen([match], {a.id: a, b.id: b}, eligibility)
    assert values[0]["disposition"] == "restricted"


@pytest.mark.parametrize("has_matches", [True, False])
def test_family_limit_bounds_proof_work_not_just_returned_rows(scenario, monkeypatch, has_matches):
    a, b, _, match, _ = scenario
    markets = {a.id: a}
    for index in range(40):
        item = b.model_copy(deep=True)
        item.id = f"inventory-{index}"
        item.rules.settlement_source = f"test-source-{index}"
        markets[item.id] = item
    if has_matches:
        markets[b.id] = b
    limit = 1 if has_matches else 3
    calls = []

    def counted(first, second):
        calls.append((first.id, second.id))
        return settlement_proof(first, second)

    monkeypatch.setattr("app.focused.settlement_proof", counted)
    result = family_screen([match] if has_matches else [], markets, profiles(), limit=limit)
    assert len(result) == limit
    assert len(calls) == limit
    assert all(row["representative_match_id"] is None for row in result) == (not has_matches)


def test_existing_valid_focus_does_not_churn_when_new_families_sort_earlier(scenario):
    a, b, books, match, _ = scenario
    other = match.model_copy(update={"id": "new-match", "event_id": "new-event"})
    families = [
        {
            "id": "a-new-family",
            "disposition": "unproven",
            "restrictions": [],
            "match_ids": [other.id],
        },
        {
            "id": "z-existing-family",
            "disposition": "unproven",
            "restrictions": [],
            "match_ids": [match.id],
        },
    ]
    selected, _ = choose_focus(families, [other, match], {a.id: a, b.id: b}, books, [match.id], 1)
    assert selected == [match.id]
    assert focused_signature([match], Venue.KALSHI) == {match.id}
    assert focused_signature([match], b.venue) == {match.id}
    assert focused_signature([match], Venue.INTERNATIONAL) == set()


async def test_postgres_connections_have_bounded_commands_and_build_identity(monkeypatch):
    import app.db as database
    from app.config import Settings

    captured = {}
    original = database.create_async_engine

    def capture(url, **kwargs):
        captured.update(kwargs)
        return original(url, **kwargs)

    monkeypatch.setattr(database, "create_async_engine", capture)
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql://test:test@localhost/test",
        build_version="test-build-identity",
        kalshi_api_key=None,
        kalshi_private_key=None,
        polymarket_us_key_id=None,
        polymarket_us_secret_key=None,
    )
    engine, _ = database.create_database(settings)
    try:
        options = captured["connect_args"]
        assert options["command_timeout"] == 60
        assert options["server_settings"] == {
            "application_name": "arbitrage:demo:test-build-i",
            "statement_timeout": "60000",
            "lock_timeout": "10000",
            "idle_in_transaction_session_timeout": "60000",
        }
    finally:
        await engine.dispose()


async def test_worker_command_timeout_is_reported_before_retry(store, monkeypatch):
    import asyncio

    calls = []

    async def timeout():
        calls.append("action")
        raise TimeoutError

    async def stop_after_error(_):
        raise asyncio.CancelledError

    monkeypatch.setattr("app.workers.asyncio.sleep", stop_after_error)
    with pytest.raises(asyncio.CancelledError):
        await guarded_loop(store, "validation-timeout-test", timeout, 0.5)
    from app.db import SystemEventRow

    async with store.sessions() as session:
        row = await session.scalar(
            select(SystemEventRow).where(SystemEventRow.role == "validation-timeout-test")
        )
        assert row.payload["error_code"] == "TimeoutError"
    assert calls == ["action"]

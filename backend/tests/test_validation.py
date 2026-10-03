from datetime import timedelta

import httpx
import pytest
from conftest import TEST_PASSWORD
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.arbitrage import calculate
from app.db import EpisodeRow, MatchRow, ShadowRow, ValidationConfigRow, ValidationRow
from app.domain import AdditionalCosts, D, Exposure, Side, Venue, now
from app.eligibility import (
    PRODUCTS,
    Eligibility,
    available_balance,
    eligibility_status,
    refresh_account_evidence,
)
from app.main import create_app
from app.shadow import complete_trial, settle_trial, shadow_tick, unwind_trial
from app.validation import diagnose, settlement_proof, validation_report, validation_tick
from app.venues.http import VenueError
from app.workers import refresh_settlements


def profiles():
    return {
        venue: Eligibility(
            venue=venue,
            product=PRODUCTS[venue],
            operator_country="US",
            operator_region="GA",
        )
        for venue in Venue
    }


def validation(scenario):
    a, b, books, match, risk = scenario
    risk.kill_switch = True
    return diagnose(match, a, b, books, risk, profiles(), now(), Exposure())[0]


def test_unapproved_match_does_not_hide_prices_or_all_other_blockers(scenario):
    a, b, books, match, risk = scenario
    match.status = "review"
    match.confidence = D("0")
    books[(a.id, Side.YES)].received_at -= timedelta(seconds=20)
    values = diagnose(match, a, b, books, risk, profiles(), now(), Exposure())
    assert len(values) == 2
    assert values[0].calculation is not None
    assert values[0].first_ask is not None
    assert "MARKET_MATCH_UNAPPROVED" in values[0].reasons
    assert "BOOK_STALE" in values[0].reasons
    assert "ORDER_PERMISSION_UNVERIFIED" in values[0].execution_reasons
    assert not values[0].executable_for_operator
    assert not values[0].shadow_qualified


def test_each_direction_uses_asks_and_matched_depth(scenario):
    a, b, books, match, risk = scenario
    values = diagnose(match, a, b, books, risk, profiles(), now(), Exposure())
    assert [(value.first_outcome, value.second_outcome) for value in values] == [
        (Side.YES, Side.NO),
        (Side.NO, Side.YES),
    ]
    first = values[0]
    assert first.first_ask == books[(a.id, Side.YES)].asks[0].price
    assert first.second_ask == books[(b.id, Side.NO)].asks[0].price
    assert first.calculation.quantity > 0
    assert sum(x.quantity for x in first.calculation.consumed_one) == first.calculation.quantity
    assert sum(x.quantity for x in first.calculation.consumed_two) == first.calculation.quantity


def test_costs_reduce_executable_profit_without_changing_depth(scenario):
    a, b, books, _, risk = scenario
    before = calculate(books[(a.id, Side.YES)], books[(b.id, Side.NO)], a, b, risk)
    risk.additional_costs = {
        a.venue: AdditionalCosts(
            verified=True,
            settlement_per_contract=D("0.01"),
            rebalancing_per_contract=D("0.02"),
            fixed_per_leg=D("0.50"),
            evidence="Test cost model",
        ),
    }
    after = calculate(books[(a.id, Side.YES)], books[(b.id, Side.NO)], a, b, risk)
    assert after.additional_cost_one == after.quantity * D("0.03") + D("0.50")
    assert after.net_profit < before.net_profit
    assert after.cost_one + after.fee_one + after.additional_cost_one <= risk.max_per_venue


def test_scenario_matrix_rejects_draw_and_void_holes(scenario):
    a, b, _, _, _ = scenario
    assert settlement_proof(a, b)["proven"]
    b.rules.cancellation = "fair_price"
    proof = settlement_proof(a, b)
    assert not proof["proven"]
    assert any(not row["covered"] for row in proof["scenarios"])


async def test_international_price_access_does_not_remove_us_restriction(store):
    values = await eligibility_status(store)
    item = values[Venue.INTERNATIONAL]
    assert item.jurisdiction_status == "close_only"
    assert item.order_permission == "restricted"
    assert not item.open_order_eligible
    assert not item.live_execution_available
    assert "VENUE_CLOSE_ONLY_US_OPERATOR" in item.reasons


def test_balances_are_not_permissions_and_are_exact_decimal():
    assert available_balance(Venue.KALSHI, {"balance": 12345}) == D("123.45")
    assert available_balance(
        Venue.US,
        {
            "balances": [{"currency": "USD", "buyingPower": "123.456789"}],
        },
    ) == D("123.456789")
    with pytest.raises(VenueError):
        available_balance(Venue.US, {"balances": []})


def test_public_shadow_requires_independent_review_even_with_complete_rules(scenario):
    a, b, books, match, risk = scenario
    a.source = b.source = "public"
    risk.additional_costs = {
        market.venue: AdditionalCosts(verified=True, evidence="Test verified zero costs")
        for market in (a, b)
    }
    assert not risk.require_human_review
    result = diagnose(match, a, b, books, risk, profiles(), now(), Exposure())[0]
    assert "HUMAN_REVIEW_REQUIRED" in result.shadow_reasons
    assert not result.shadow_qualified


def test_unknown_fees_do_not_invent_a_depth_failure(scenario):
    a, b, books, match, risk = scenario
    a.fee.kind = "unknown"
    result = diagnose(match, a, b, books, risk, profiles(), now(), Exposure())[0]
    assert "UNKNOWN_OR_EXPIRED_FEE" in result.reasons
    assert "INSUFFICIENT_DEPTH_OR_CAPITAL" not in result.reasons


async def test_successful_account_probes_are_get_only_and_never_order_permission(
    store, monkeypatch
):
    calls = []
    monkeypatch.setattr("app.eligibility.kalshi_headers", lambda *args: {})
    monkeypatch.setattr("app.eligibility.us_headers", lambda *args: {})

    def respond(request):
        calls.append((request.method, str(request.url)))
        return httpx.Response(
            200,
            json=(
                {"balance": 12345}
                if request.url.host == "external-api.kalshi.com"
                else {"balances": [{"currency": "USD", "buyingPower": "17.123456789"}]}
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        await refresh_account_evidence(store, client)
    assert calls == [
        ("GET", "https://external-api.kalshi.com/trade-api/v2/portfolio/balance"),
        ("GET", "https://api.polymarket.us/v1/account/balances"),
    ]
    values = await eligibility_status(store)
    assert values[Venue.US].available_balance == D("17.123456789")
    assert values[Venue.KALSHI].account_read_access == "verified"
    assert values[Venue.KALSHI].order_permission == "unverified"
    assert not values[Venue.KALSHI].open_order_eligible


async def test_read_only_probe_failure_is_persisted_not_success_shaped(store):
    async with httpx.AsyncClient() as client:
        await refresh_account_evidence(store, client)
    status = await eligibility_status(store)
    assert status[Venue.KALSHI].account_read_access == "failed"
    assert not status[Venue.KALSHI].open_order_eligible
    assert status[Venue.US].available_balance is None


async def test_diagnostics_persist_while_global_kill_switch_stays_active(store):
    await validation_tick(store)
    report = await validation_report(store)
    assert report["candidate_pairs"] == len(await store.matches())
    assert report["directions"] == report["candidate_pairs"] * 2
    assert report["rejection_counts"]["KILL_SWITCH_ACTIVE"] == report["candidate_pairs"]
    assert not report["automatic_live_permission"]
    assert not report["diagnostic_window_complete"]
    assert not report["profit_after_operating_cost"]
    assert (await store.risk())[0].kill_switch
    assert len(report["settings"]["selected_match_ids"]) <= 20
    async with store.sessions() as session:
        assert (await session.scalars(select(ValidationRow))).all()


async def test_episodes_deduplicate_changing_book_observations(store):
    await validation_tick(store)
    async with store.sessions() as session:
        before = (await session.scalars(select(EpisodeRow))).all()
        count = len(before)
        observations = sum(row.payload["observations"] for row in before)
    assert count > 0
    await validation_tick(store)
    async with store.sessions() as session:
        after = (await session.scalars(select(EpisodeRow))).all()
        assert len(after) == count
        assert sum(row.payload["observations"] for row in after) > observations


async def test_shadow_intents_are_bounded_distinct_and_restart_safe(store):
    await validation_tick(store)
    await shadow_tick(store)
    async with store.sessions() as session:
        before = (await session.scalars(select(ShadowRow))).all()
    assert before
    assert {row.payload["scenario"] for row in before} == {
        "baseline",
        "second_leg_rejected",
        "partial_second_leg",
    }
    assert all(D(row.payload["reserved_capital"]) <= 50 for row in before)
    await shadow_tick(store)
    async with store.sessions() as session:
        after = (await session.scalars(select(ShadowRow))).all()
        assert len(after) == len(before)
    assert (await store.risk())[0].kill_switch
    assert not await store.paper_trades()


async def test_second_leg_rejection_has_unwind_loss_not_success_profit(store):
    await validation_tick(store)
    await shadow_tick(store)
    async with store.sessions() as session:
        row = next(
            row
            for row in (await session.scalars(select(ShadowRow))).all()
            if row.payload["scenario"] == "second_leg_rejected"
        )
    payload = row.payload
    markets = {market.id: market for market in await store.markets()}
    signal = payload["validation"]
    a, b = markets[signal["first_market_id"]], markets[signal["second_market_id"]]
    books = await store.books()
    at = now() + timedelta(seconds=1)
    for book in books.values():
        book.received_at = book.exchange_at = at
    state, filled = complete_trial(payload, a, b, books, at)
    assert state == "UNWIND_PENDING"
    assert filled["second"]["quantity"] == "0"
    later = at + timedelta(seconds=1)
    for book in books.values():
        book.received_at = book.exchange_at = later
    state, closed = unwind_trial(filled, a, b, books, later)
    assert state in ("UNWOUND", "RESIDUAL_EXPOSURE")
    assert D(closed["net_pnl"]) < 0
    assert closed["pnl_basis"] != "hypothetical_locked_payout_not_settled"


async def test_partial_hedge_keeps_capital_until_observed_settlement(store):
    await validation_tick(store)
    await shadow_tick(store)
    async with store.sessions() as session:
        row = next(
            row
            for row in (await session.scalars(select(ShadowRow))).all()
            if row.payload["scenario"] == "partial_second_leg"
        )
    payload = row.payload
    markets = {market.id: market for market in await store.markets()}
    signal = payload["validation"]
    a, b = markets[signal["first_market_id"]], markets[signal["second_market_id"]]
    books = await store.books()
    instant = now() + timedelta(seconds=1)
    for book in books.values():
        book.received_at = book.exchange_at = instant
    state, filled = complete_trial(payload, a, b, books, instant)
    assert state == "UNWIND_PENDING"
    assert D(filled["second"]["quantity"]) > 0
    later = instant + timedelta(seconds=1)
    for book in books.values():
        book.received_at = book.exchange_at = later
        for level in book.bids:
            level.quantity = D("1000")
    state, held = unwind_trial(filled, a, b, books, later)
    assert state == "HEDGED_AFTER_UNWIND"
    assert "closed_at" not in held
    assert all(D(level["quantity"]) % a.quantity_step == 0 for level in held["unwind_levels"])
    a.result, b.result = D("1"), D("1")
    settled = settle_trial(held, a, b, later + timedelta(hours=1))
    assert settled["pnl_basis"] == "hypothetical_observed_settlement"
    assert settled["closed_at"]
    payout = D(filled["first"]["quantity"]) - D(held["unwind_quantity"])
    assert D(settled["net_pnl"]) == (
        payout
        + D(held["unwind_proceeds"])
        - D(held["spent"])
        - D(held["unwind_fee"])
        - D(held["execution_slippage"])
    )


async def test_manual_rejection_cancels_pending_shadow_intents(store):
    await validation_tick(store)
    await shadow_tick(store)
    async with store.sessions.begin() as session:
        matches = (await session.scalars(select(MatchRow))).all()
        for row in matches:
            row.payload = {**row.payload, "status": "rejected"}
        trials = (await session.scalars(select(ShadowRow))).all()
        for trial in trials:
            trial.due_at = now() - timedelta(seconds=1)
    await shadow_tick(store)
    async with store.sessions() as session:
        trials = (await session.scalars(select(ShadowRow))).all()
        assert trials and all(row.state == "CANCELLED" for row in trials)
        assert all(
            row.payload["failure_reason"] == "CURRENT_MATCH_APPROVAL_REQUIRED" for row in trials
        )


async def test_virtual_kalshi_cap_is_shared_across_both_polymarket_products(store):
    store.settings.operator_country = "CA"
    async with store.sessions.begin() as session:
        row = await session.get(ValidationConfigRow, store.source)
        row.payload = {**row.payload, "virtual_balance_per_venue": "50"}
    await validation_tick(store)
    await shadow_tick(store)
    async with store.sessions() as session:
        baseline = [
            row
            for row in (await session.scalars(select(ShadowRow))).all()
            if row.payload["scenario"] == "baseline"
        ]
        assert baseline
        assert sum(D(row.payload["reserved_capital"]) for row in baseline) <= D("50")


async def test_settlement_polling_includes_held_shadow_markets(store, monkeypatch):
    await validation_tick(store)
    await shadow_tick(store)
    async with store.sessions.begin() as session:
        rows = (await session.scalars(select(ShadowRow))).all()
        row = rows[0]
        row.state = "HEDGED_AFTER_UNWIND"
        identifiers = {
            row.payload["validation"]["first_market_id"],
            row.payload["validation"]["second_market_id"],
        }
    requested = set()

    async def settlement(client, market):
        requested.add(market.id)
        return None

    for name in ("KalshiClient", "USClient", "InternationalClient"):
        monkeypatch.setattr(f"app.workers.{name}.get_settlement", settlement)
    await refresh_settlements(store)
    assert requested == identifiers


async def test_validation_api_auth_csrf_geo_and_revision_guards(store):
    app = create_app(store.settings)
    app.state.store = store
    await validation_tick(store)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        assert (await client.get("/api/v1/validation/summary")).status_code == 200
        assert (await client.put("/api/v1/validation/configuration", json={})).status_code == 401
        login = (await client.post("/api/v1/auth/login", json={"password": TEST_PASSWORD})).json()
        headers = {"X-CSRF-Token": login["csrf"]}
        report = (await client.get("/api/v1/validation/summary")).json()
        body = {"revision": report["revision"], "settings": report["settings"], "reason": "Test"}
        assert (await client.put("/api/v1/validation/configuration", json=body)).status_code == 403
        body["revision"] -= 1
        assert (
            await client.put(
                "/api/v1/validation/configuration",
                json=body,
                headers=headers,
            )
        ).status_code == 409
        response = await client.put(
            "/api/v1/validation/eligibility/polymarket_international",
            json={
                "jurisdiction_confirmed": True,
                "kyc_confirmed": True,
                "order_permission_confirmed": True,
                "market_restrictions_reviewed": True,
                "expires_at": (now() + timedelta(days=1)).isoformat(),
                "evidence": "Cannot override US",
            },
            headers=headers,
        )
        assert response.status_code == 422
        assert "CLOSE_ONLY" in response.text
        assert (await client.get("/api/v1/validation/candidates?limit=1")).json()["total"] > 0

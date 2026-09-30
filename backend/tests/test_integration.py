from datetime import timedelta

from conftest import TEST_PASSWORD
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db import OpportunityRow, RiskRow
from app.domain import Opportunity, PaperState, RiskSettings, now
from app.main import create_app
from app.service import analysis_tick, reserve_trade


async def enable(store):
    async with store.sessions.begin() as session:
        row = await session.get(RiskRow, store.source)
        row.payload = RiskSettings(kill_switch=False).model_dump(mode="json")


async def test_real_pipeline_persistence_and_restart(store):
    await enable(store)
    await analysis_tick(store)
    trades = await store.paper_trades()
    assert len(trades) == 4
    assert all(x.state == PaperState.SUBMITTED for x in trades)
    from app.store import Store

    restarted = Store(store.sessions, store.settings)
    await restarted.initialize()
    assert len(await restarted.paper_trades()) == 4
    latest = await store.books()
    at = max(x.due_at for x in trades) + timedelta(milliseconds=1)
    for book in latest.values():
        book.received_at = book.exchange_at = at
    await store.save_books(list(latest.values()))
    import asyncio

    await asyncio.sleep(max(0, (at - now()).total_seconds()))
    await analysis_tick(restarted)
    after = await restarted.paper_trades()
    assert all(x.state == PaperState.HEDGED for x in after)
    await analysis_tick(restarted)
    assert len(await restarted.paper_trades()) == 4


async def test_default_kill_switch_never_trades(store):
    await analysis_tick(store)
    assert not await store.paper_trades()
    async with store.sessions() as session:
        assert not (
            await session.scalars(
                select(OpportunityRow).where(OpportunityRow.status == "qualified")
            )
        ).all()


async def test_intent_idempotency(store):
    await enable(store)
    await analysis_tick(store)
    async with store.sessions() as session:
        rows = (await session.scalars(select(OpportunityRow))).all()
    op = Opportunity.model_validate(next(x.payload for x in rows if x.status == "qualified"))
    assert not await reserve_trade(store, op)
    assert len(await store.paper_trades()) == 4


async def test_worker_lease(store):
    assert await store.heartbeat("analysis", "first", acquire=True)
    assert not await store.heartbeat("analysis", "second", acquire=True)
    assert not await store.heartbeat("analysis", "second")
    await store.release("analysis", "first")
    assert await store.heartbeat("analysis", "second", acquire=True)


async def test_unhedged_fill_trips_persisted_circuit_breaker(store):
    from app.paper import evaluate_paper

    await enable(store)
    await analysis_tick(store)
    trade = (await store.paper_trades())[0]
    async with store.sessions() as session:
        row = await session.get(OpportunityRow, trade.opportunity_id)
        opportunity = Opportunity.model_validate(row.payload)
    markets = {market.id: market for market in await store.markets()}
    books = await store.books()
    at = trade.due_at + timedelta(milliseconds=1)
    for book in books.values():
        book.received_at = book.exchange_at = at
    books[(opportunity.second_market_id, opportunity.second_outcome)].asks = []
    assert evaluate_paper(
        trade,
        opportunity,
        books,
        markets[opportunity.first_market_id],
        markets[opportunity.second_market_id],
        at,
        False,
    )
    assert trade.state == PaperState.UNHEDGED
    assert await store.save_trade(trade, PaperState.SUBMITTED)
    settings, revision = await store.risk()
    assert settings.kill_switch
    assert revision > 1


async def test_admin_auth_csrf_settings_audit_and_health(store):
    app = create_app(store.settings)
    app.state.store = store
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        assert (await client.get("/health/ready")).status_code == 200
        assert (await client.post("/api/v1/settings/kill-switch/deactivate")).status_code == 401
        login = await client.post("/api/v1/auth/login", json={"password": TEST_PASSWORD})
        assert login.status_code == 200, login.text
        assert "HttpOnly" in login.headers["set-cookie"]
        assert "SameSite=strict" in login.headers["set-cookie"]
        assert (await client.post("/api/v1/settings/kill-switch/deactivate")).status_code == 403
        headers = {"X-CSRF-Token": login.json()["csrf"]}
        result = await client.post("/api/v1/settings/kill-switch/deactivate", headers=headers)
        assert result.status_code == 200
        value = result.json()
        response = await client.put(
            "/api/v1/settings/risk",
            json={
                "revision": value["revision"] - 1,
                "settings": value["settings"],
            },
            headers=headers,
        )
        assert response.status_code == 409
        response = await client.post(
            "/api/v1/settings/kill-switch/activate",
            headers={**headers, "Origin": "https://evil.invalid"},
        )
        assert response.status_code == 403
        audits = (await client.get("/api/v1/audit")).json()
        assert any(x["action"] == "kill_switch" for x in audits["items"])
        config = (await client.get("/api/v1/system/configuration")).json()
        assert not config["live_execution_available"]
        assert "session_secret" not in config
        for path in (
            "/markets",
            "/matches",
            "/events",
            "/paper-trades",
            "/opportunities",
            "/alerts",
        ):
            page = await client.get(f"/api/v1{path}?limit=1&offset=0")
            assert page.status_code == 200, page.text
            assert len(page.json()["items"]) <= (100 if path == "/alerts" else 1)
        assert (await client.post("/api/v1/auth/logout", headers=headers)).status_code == 200
        assert (await client.get("/api/v1/audit")).status_code == 401


async def test_body_limit_and_auth_rate_limit(store):
    app = create_app(store.settings)
    app.state.store = store
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        assert (
            await client.post("/api/v1/auth/login", content=b"x" * 1_000_001)
        ).status_code == 413
        for _ in range(5):
            assert (
                await client.post("/api/v1/auth/login", json={"password": "wrong"})
            ).status_code == 401
        assert (
            await client.post("/api/v1/auth/login", json={"password": "wrong"})
        ).status_code == 429


async def test_validation_never_reflects_password(store):
    app = create_app(store.settings)
    app.state.store = store
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        password = "sensitive-test-input-" * 30
        response = await client.post("/api/v1/auth/login", json={"password": password})
        assert response.status_code == 422
        assert password not in response.text


async def test_unknown_fields_cannot_be_approved(store):
    app = create_app(store.settings)
    app.state.store = store
    review = next(x for x in await store.matches() if x.status == "review")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        login = (await client.post("/api/v1/auth/login", json={"password": TEST_PASSWORD})).json()
        result = await client.post(
            f"/api/v1/matches/{review.id}/approve",
            json={
                "expected_first_rules_hash": review.first_rules_hash,
                "expected_second_rules_hash": review.second_rules_hash,
                "note": "Try unsafe approval",
            },
            headers={"X-CSRF-Token": login["csrf"]},
        )
        assert result.status_code == 422
        assert "DETERMINISTIC_GATES_FAILED" in result.text

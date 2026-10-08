import copy
import socket
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.deployment import configuration_identity, worker_heartbeats


def health():
    return {
        "dependencies": {"ready": True},
        "workers": [
            {
                "role": role,
                "healthy": True,
                "status": "running",
                "build": "new-release",
                "source": "public",
                "at": datetime.now(UTC).isoformat(),
            }
            for role in ("market-data", "analysis", "maintenance")
        ],
    }


def test_configuration_requires_expected_safe_release():
    value = {
        "trading_mode": "paper",
        "live_execution_available": False,
        "data_mode": "public",
        "build_version": "new-release",
    }
    assert configuration_identity(value, "new-release") == ("new-release", "public")
    with pytest.raises(ValueError, match="API_VERSION_MISMATCH"):
        configuration_identity(value, "other-release")
    for field, replacement in (
        ("trading_mode", "live"),
        ("live_execution_available", True),
        ("data_mode", "unknown"),
        ("build_version", None),
    ):
        with pytest.raises(ValueError, match="CONFIGURATION_INVALID"):
            configuration_identity({**value, field: replacement})


def test_worker_health_requires_all_roles_and_advancing_same_version_leases():
    value = health()
    previous = worker_heartbeats(value, "new-release", "public")
    with pytest.raises(ValueError, match="HEARTBEAT_NOT_ADVANCING"):
        worker_heartbeats(value, "new-release", "public", previous)
    for worker in value["workers"]:
        worker["at"] = (previous[worker["role"]] + timedelta(seconds=7)).isoformat()
    assert set(worker_heartbeats(value, "new-release", "public", previous)) == set(previous)


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ("missing", "REQUIRED_WORKER_MISSING"),
        ("duplicate", "ROLE_DUPLICATE"),
        ("old", "IDENTITY_MISMATCH"),
        ("mixed_source", "IDENTITY_MISMATCH"),
        ("expired", "UNHEALTHY"),
        ("stopped", "UNHEALTHY"),
        ("bad_timestamp", "TIMESTAMP_INVALID"),
        ("naive_timestamp", "TIMESTAMP_INVALID"),
        ("dependency", "DEPENDENCIES_NOT_READY"),
        ("invalid_role", "ROLE_INVALID"),
    ],
)
def test_worker_deployment_faults_fail_closed(change, code):
    value = health()
    worker = value["workers"][0]
    if change == "missing":
        value["workers"].pop()
    elif change == "duplicate":
        value["workers"].append(copy.deepcopy(worker))
    elif change == "old":
        worker["build"] = "old-release"
    elif change == "mixed_source":
        worker["source"] = "demo"
    elif change == "expired":
        worker["healthy"] = False
    elif change == "stopped":
        worker["status"] = "stopped"
    elif change == "bad_timestamp":
        worker["at"] = "invalid"
    elif change == "naive_timestamp":
        worker["at"] = "2026-10-08T16:00:00"
    elif change == "dependency":
        value["dependencies"]["ready"] = False
    else:
        worker["role"] = ["analysis"]
    with pytest.raises(ValueError, match=code):
        worker_heartbeats(value, "new-release", "public")


def unavailable_app(monkeypatch, errors, **settings):
    from app import main

    engine = AsyncMock()
    session = AsyncMock()
    session.execute.side_effect = errors
    session.scalar.return_value = "93ad7c201b46"
    sessions = MagicMock()
    sessions.return_value.__aenter__.return_value = session
    monkeypatch.setattr(main, "create_database", lambda _: (engine, sessions))
    return main.create_app(Settings(_env_file=None, environment="test", **settings)), engine


@pytest.mark.parametrize(
    "error",
    [
        socket.gaierror(-2, "private-database-host"),
        ConnectionRefusedError("private-host"),
        TimeoutError("private-host"),
    ],
)
async def test_database_transport_failure_is_readiness_503_and_can_recover(monkeypatch, error):
    app, engine = unavailable_app(monkeypatch, [error, None])
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            response = await client.get("/health/ready")
            assert response.status_code == 503 and response.json()["ready"] is False
            assert "private" not in response.text
            assert (await client.get("/health/ready")).status_code == 200
    finally:
        await engine.dispose()


@pytest.mark.parametrize("stage", ["dependencies", "initialization"])
async def test_api_failed_startup_disposes_connection_pool(monkeypatch, stage):
    from app import main

    if stage == "dependencies":
        app, engine = unavailable_app(monkeypatch, [socket.gaierror(-2, "private-host")])
        code = "MIGRATIONS_OR_DEPENDENCIES_NOT_READY"
    else:
        app, engine = unavailable_app(monkeypatch, [None])
        monkeypatch.setattr(
            main.Store, "initialize", AsyncMock(side_effect=RuntimeError("INITIALIZATION_FAILED"))
        )
        code = "INITIALIZATION_FAILED"
    with pytest.raises(RuntimeError, match=code):
        async with app.router.lifespan_context(app):
            pytest.fail("Startup must not succeed")
    engine.dispose.assert_awaited_once()


async def test_api_pool_is_disposed_even_when_redis_cleanup_fails(monkeypatch):
    from app import main

    redis = AsyncMock()
    redis.ping.return_value = False
    redis.aclose.side_effect = RuntimeError("TEST_REDIS_CLOSE_FAILED")
    monkeypatch.setattr(main.Redis, "from_url", lambda *_, **__: redis)
    app, engine = unavailable_app(monkeypatch, [None], redis_url="redis://localhost:1")
    with pytest.raises(RuntimeError, match="TEST_REDIS_CLOSE_FAILED"):
        async with app.router.lifespan_context(app):
            pytest.fail("Unavailable Redis must prevent startup")
    engine.dispose.assert_awaited_once()

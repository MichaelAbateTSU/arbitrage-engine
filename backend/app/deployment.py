"""Fail-closed application evidence for deployment smoke checks."""

from datetime import datetime
from typing import Any

ROLES = {"market-data", "analysis", "maintenance"}


def configuration_identity(value: Any, expected_version: str | None = None) -> tuple[str, str]:
    if (
        not isinstance(value, dict)
        or value.get("trading_mode") != "paper"
        or value.get("live_execution_available") is not False
        or value.get("data_mode") not in ("demo", "public")
        or not isinstance(value.get("build_version"), str)
        or not value["build_version"]
    ):
        raise ValueError("DEPLOYMENT_CONFIGURATION_INVALID")
    version = value["build_version"]
    if expected_version is not None and version != expected_version:
        raise ValueError("DEPLOYMENT_API_VERSION_MISMATCH")
    return version, value["data_mode"]


def worker_heartbeats(
    value: Any,
    version: str,
    source: str,
    previous: dict[str, datetime] | None = None,
) -> dict[str, datetime]:
    if not isinstance(value, dict) or not isinstance(value.get("workers"), list):
        raise ValueError("DEPLOYMENT_WORKERS_INVALID")
    dependencies = value.get("dependencies")
    if not isinstance(dependencies, dict) or dependencies.get("ready") is not True:
        raise ValueError("DEPLOYMENT_DEPENDENCIES_NOT_READY")
    result: dict[str, datetime] = {}
    for worker in value["workers"]:
        if (
            not isinstance(worker, dict)
            or not isinstance(worker.get("role"), str)
            or worker["role"] not in ROLES
        ):
            raise ValueError("DEPLOYMENT_WORKER_ROLE_INVALID")
        role = worker["role"]
        if role in result:
            raise ValueError("DEPLOYMENT_WORKER_ROLE_DUPLICATE")
        if worker.get("healthy") is not True or worker.get("status") != "running":
            raise ValueError("DEPLOYMENT_WORKER_UNHEALTHY")
        if worker.get("build") != version or worker.get("source") != source:
            raise ValueError("DEPLOYMENT_WORKER_IDENTITY_MISMATCH")
        if not isinstance(worker.get("at"), str):
            raise ValueError("DEPLOYMENT_WORKER_TIMESTAMP_INVALID")
        try:
            at = datetime.fromisoformat(worker["at"])
        except ValueError as exc:
            raise ValueError("DEPLOYMENT_WORKER_TIMESTAMP_INVALID") from exc
        if at.tzinfo is None:
            raise ValueError("DEPLOYMENT_WORKER_TIMESTAMP_INVALID")
        if previous is not None and (role not in previous or at <= previous[role]):
            raise ValueError("DEPLOYMENT_WORKER_HEARTBEAT_NOT_ADVANCING")
        result[role] = at
    if set(result) != ROLES:
        raise ValueError("DEPLOYMENT_REQUIRED_WORKER_MISSING")
    return result

import argparse
import os
import time

import httpx

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:8000")
parser.add_argument("--workers", action="store_true")
args = parser.parse_args()
with httpx.Client(base_url=args.url, timeout=20, follow_redirects=False) as client:
    for path in ("/health/live", "/health/ready", "/health/dependencies", "/"):
        response = client.get(path)
        response.raise_for_status()
        if path != "/":
            assert response.json().get("ready", True)
        print("PASSED", path)
    password = os.environ.get("ARB_SMOKE_PASSWORD")
    if password:
        response = client.post("/api/v1/auth/login", json={"password": password})
        response.raise_for_status()
    for path in (
        "/system/configuration",
        "/system/health",
        "/markets?limit=1",
        "/matches?limit=1",
        "/opportunities?limit=1",
        "/paper-trades/stats",
    ):
        response = client.get("/api/v1" + path)
        response.raise_for_status()
        print("PASSED", "/api/v1" + path)
        if path == "/system/configuration":
            assert response.json()["live_execution_available"] is False
        if path == "/system/health" and args.workers:
            workers = response.json()["workers"]
            assert len(workers) >= 3 and all(x["healthy"] for x in workers)
            previous = {worker["role"]: worker["at"] for worker in workers}
            time.sleep(7)
            refreshed = client.get("/api/v1/system/health")
            refreshed.raise_for_status()
            assert all(
                worker["healthy"] and worker["at"] > previous[worker["role"]]
                for worker in refreshed.json()["workers"]
            ), (
                "Worker heartbeats did not advance; cached lease is not running-process proof"
            )
    with client.stream("GET", "/api/v1/stream") as response:
        response.raise_for_status()
        assert any(line.startswith("data:") for line in response.iter_lines())
    print("PASSED server-sent events")

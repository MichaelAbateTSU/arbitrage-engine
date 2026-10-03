"""Single-process local demo launcher; production uses separate Render roles."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import uvicorn
from app.config import get_settings
from app.db import create_database
from app.main import create_app
from app.store import Store
from app.workers import role_runner


async def main():
    settings = get_settings()
    if settings.data_mode != "demo" or settings.environment not in (
        "development",
        "test",
    ):
        raise RuntimeError("LOCAL_DEMO_LAUNCHER_ONLY")
    root = Path(__file__).resolve().parent.parent
    await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(root / "backend" / "alembic.ini"),
            "upgrade",
            "head",
        ],
        cwd=root / "backend",
        check=True,
        env=os.environ.copy(),
    )
    engine, sessions = create_database(settings)
    store = Store(sessions, settings)
    await store.initialize()
    tasks = [
        asyncio.create_task(role_runner(role, store, settings))
        for role in ("market-data", "analysis", "maintenance")
    ]
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings),
            host="127.0.0.1",
            port=int(os.environ.get("DEMO_PORT", "8000")),
        )
    )

    def worker_finished(task):
        if not task.cancelled() and task.exception():
            print("Demo worker failed:", type(task.exception()).__name__)
            server.should_exit = True

    for task in tasks:
        task.add_done_callback(worker_finished)
    try:
        await server.serve()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await engine.dispose()


asyncio.run(main())

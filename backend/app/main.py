import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from inspect import isawaitable
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api import auth_router, router
from app.config import Settings, get_settings
from app.db import create_database
from app.store import Store
from app.telemetry import LATENCY, configure_logging
from app.validation_api import router as validation_router

log = structlog.get_logger()


class RequestLimit:
    def __init__(self, app: ASGIApp, maximum: int = 1_000_000) -> None:
        self.app = app
        self.maximum = maximum

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        size = 0
        messages = []
        while True:
            message = await receive()
            size += len(message.get("body", b""))
            if size > self.maximum:
                response = JSONResponse({"detail": "REQUEST_TOO_LARGE"}, status_code=413)
                await response(scope, receive, send)
                return
            messages.append(message)
            if not message.get("more_body", False):
                break

        async def buffered() -> Message:
            if messages:
                return messages.pop(0)
            return await receive()

        await self.app(scope, buffered, send)


class SecurityHeaders:
    def __init__(self, app: ASGIApp, production: bool) -> None:
        self.app = app
        self.production = production

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        correlation = uuid4().hex
        begin = time.monotonic()
        headers = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "X-Request-ID": correlation,
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        }
        if not scope["path"].startswith(("/docs", "/redoc")):
            headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                "base-uri 'none'; form-action 'self'"
            )
        if self.production:
            headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if scope["path"].startswith("/api"):
            headers["Cache-Control"] = "no-store"

        async def secured(message: Message) -> None:
            if message["type"] == "http.response.start":
                LATENCY.observe(time.monotonic() - begin)
                message["headers"] = [
                    *message.get("headers", []),
                    *((key.lower().encode(), value.encode()) for key, value in headers.items()),
                ]
            await send(message)

        await self.app(scope, receive, secured)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or get_settings()
    configure_logging()
    engine, sessions = create_database(config)
    store = Store(sessions, config)
    redis: Redis | None = None
    if config.redis_url:
        redis = Redis.from_url(
            config.redis_url.get_secret_value(), socket_timeout=3, socket_connect_timeout=3
        )

    async def dependencies() -> dict[str, Any]:
        result: dict[str, Any] = {
            "database": "unavailable",
            "schema": "unavailable",
            "redis": "not_configured",
            "ready": False,
            "trading_mode": config.trading_mode,
        }
        try:
            async with sessions() as session:
                await session.execute(text("SELECT 1"))
                result["database"] = "healthy"
                revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
                result["schema"] = str(revision or "unavailable")
                result["ready"] = revision == "93ad7c201b46"
        except (SQLAlchemyError, OSError, TimeoutError):
            log.error("dependency_failed", error_code="DATABASE_OR_MIGRATION_UNAVAILABLE")
        if redis:
            from redis.exceptions import RedisError

            try:
                ping = redis.ping()
                if not isawaitable(ping):
                    log.error("dependency_failed", error_code="ASYNC_REDIS_CLIENT_REQUIRED")
                    raise RuntimeError("ASYNC_REDIS_CLIENT_REQUIRED")
                result["redis"] = "healthy" if await ping else "unavailable"
            except RedisError:
                result["redis"] = "unavailable"
            result["ready"] = result["ready"] and result["redis"] == "healthy"
        return result

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            status = await dependencies()
            if not status["ready"]:
                raise RuntimeError("MIGRATIONS_OR_DEPENDENCIES_NOT_READY")
            await store.initialize()
            yield
        finally:
            try:
                if redis:
                    await redis.aclose()
            finally:
                await engine.dispose()

    app = FastAPI(
        title="Arbitrage Intelligence API",
        version="0.1.0",
        description="Read-only sports contracts and hypothetical paper execution. No live orders.",
        lifespan=lifespan,
    )
    app.include_router(validation_router)
    app.state.settings = config
    app.state.store = store
    app.state.dependencies = dependencies
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )
    app.add_middleware(RequestLimit)
    app.add_middleware(SecurityHeaders, production=config.environment == "production")

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        log.error("api_database_error", error_code=type(exc).__name__)
        return JSONResponse({"detail": "DATABASE_UNAVAILABLE"}, status_code=503)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {
                "detail": [
                    {"loc": error["loc"], "type": error["type"], "msg": error["msg"]}
                    for error in exc.errors()
                ],
            },
            status_code=422,
        )

    @app.get("/health/live")
    @app.get("/api/v1/health/live")
    async def live() -> dict[str, Any]:
        return {"status": "alive", "trading_mode": "paper", "live_execution_available": False}

    @app.get("/health/ready")
    @app.get("/api/v1/health/ready")
    async def ready() -> JSONResponse:
        status = await dependencies()
        return JSONResponse(status, status_code=200 if status["ready"] else 503)

    @app.get("/health/dependencies")
    @app.get("/api/v1/health/dependencies")
    async def deps() -> dict[str, Any]:
        return await dependencies()

    app.include_router(auth_router)
    app.include_router(router)
    dist = Path(config.frontend_dist).resolve()
    if dist.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def frontend(path: str) -> FileResponse:
            if path.startswith(("api/", "health/")):
                raise HTTPException(404, "NOT_FOUND")
            return FileResponse(dist / "index.html")

    return app


app = create_app()

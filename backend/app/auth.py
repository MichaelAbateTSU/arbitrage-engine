import hashlib
import secrets
import time
from typing import Any
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request, Response
from pydantic import Field, SecretStr
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db import AuthAttemptRow, SessionRow
from app.domain import Model
from app.store import Store, audit

COOKIE = "arb_session"
hasher = PasswordHasher()


class Login(Model):
    password: SecretStr = Field(min_length=1, max_length=256)


async def rate_limit(store: Store, key: str, limit: int, seconds: int) -> None:
    identifier = hashlib.sha256(key.encode()).hexdigest()
    epoch = int(time.time())
    async with store.sessions.begin() as session:
        bind = session.get_bind()
        insert = sqlite_insert if bind.dialect.name == "sqlite" else pg_insert
        await session.execute(
            insert(AuthAttemptRow)
            .values(
                id=identifier,
                attempts=0,
                expires_epoch=epoch + seconds,
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        row = await session.get(AuthAttemptRow, identifier, with_for_update=True)
        if row is None:
            raise HTTPException(503, "RATE_LIMIT_STORE_UNAVAILABLE")
        if row.expires_epoch <= epoch:
            row.attempts = 0
            row.expires_epoch = epoch + seconds
        if row.attempts >= limit:
            raise HTTPException(429, "RATE_LIMIT_EXCEEDED")
        row.attempts += 1


async def session_identity(request: Request) -> dict[str, Any] | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    identifier = hashlib.sha256(token.encode()).hexdigest()
    store: Store = request.app.state.store
    async with store.sessions() as session:
        row = await session.get(SessionRow, identifier)
        generation = hashlib.sha256(
            (
                store.settings.session_secret.get_secret_value()
                if store.settings.session_secret
                else "development"
            ).encode()
        ).hexdigest()
        if (
            not row
            or row.expires_epoch <= int(time.time())
            or row.source != store.source
            or row.payload.get("generation") != generation
        ):
            return None
        return row.payload


async def reader(request: Request) -> None:
    settings = request.app.state.settings
    if settings.public_read_enabled or settings.environment in ("development", "test"):
        return
    if await session_identity(request) is None:
        raise HTTPException(401, "AUTHENTICATION_REQUIRED")


async def admin(request: Request) -> str:
    identity = await session_identity(request)
    if identity is None:
        raise HTTPException(401, "AUTHENTICATION_REQUIRED")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        csrf = request.headers.get("X-CSRF-Token", "")
        if not secrets.compare_digest(csrf, identity["csrf"]):
            raise HTTPException(403, "CSRF_VALIDATION_FAILED")
        origin = request.headers.get("origin")
        if origin and origin not in request.app.state.settings.origins:
            raise HTTPException(403, "ORIGIN_NOT_ALLOWED")
    return str(identity["actor"])


async def login(request: Request, response: Response, body: Login) -> dict[str, Any]:
    store: Store = request.app.state.store
    origin = request.headers.get("origin")
    if origin and origin not in store.settings.origins:
        raise HTTPException(403, "ORIGIN_NOT_ALLOWED")
    ip = request.client.host if request.client else "unknown"
    await rate_limit(store, f"login:{ip}", 5, 60)
    configured = store.settings.admin_password_hash
    if configured is None:
        raise HTTPException(503, "ADMIN_PASSWORD_NOT_CONFIGURED")
    valid = False
    try:
        valid = hasher.verify(configured.get_secret_value(), body.password.get_secret_value())
    except (VerificationError, InvalidHashError):
        valid = False
    if not valid:
        async with store.sessions.begin() as session:
            audit(
                session, store.source, "login_failed", "anonymous", {"code": "INVALID_CREDENTIALS"}
            )
        raise HTTPException(401, "INVALID_CREDENTIALS")
    token = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    identifier = hashlib.sha256(token.encode()).hexdigest()
    expires = int(time.time()) + 8 * 3600
    async with store.sessions.begin() as session:
        session.add(
            SessionRow(
                id=identifier,
                source=store.source,
                expires_epoch=expires,
                payload={
                    "actor": "admin",
                    "csrf": csrf,
                    "expires_epoch": expires,
                    "generation": hashlib.sha256(
                        (
                            store.settings.session_secret.get_secret_value()
                            if store.settings.session_secret
                            else "development"
                        ).encode()
                    ).hexdigest(),
                },
            )
        )
        audit(session, store.source, "login_success", "admin", {"session": uuid4().hex})
    response.set_cookie(
        COOKIE,
        token,
        max_age=8 * 3600,
        httponly=True,
        secure=store.settings.environment == "production",
        samesite="strict",
        path="/",
    )
    return {"authenticated": True, "csrf": csrf, "actor": "admin"}

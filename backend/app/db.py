from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    event,
)
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import Settings
from app.domain import now


class Base(DeclarativeBase):
    pass


class Record(Base):
    __abstract__ = True
    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    source: Mapped[str] = mapped_column(String(16), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class CanonicalEventRow(Record, Base):
    __tablename__ = "canonical_sports_events"
    league: Mapped[str] = mapped_column(String(32), index=True)


class MarketRow(Record, Base):
    __tablename__ = "venue_markets"
    venue: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(256))
    league: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    rules_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("venue", "external_id", "source"),)


class SpecificationRow(Record, Base):
    __tablename__ = "contract_specification_versions"
    market_id: Mapped[str] = mapped_column(ForeignKey("venue_markets.id"), index=True)
    rules_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("market_id", "rules_hash"),)


class AnnotationRow(Record, Base):
    __tablename__ = "market_annotations"
    market_id: Mapped[str] = mapped_column(ForeignKey("venue_markets.id"), unique=True)
    raw_rules_hash: Mapped[str] = mapped_column(String(64))


class AliasRow(Record, Base):
    __tablename__ = "team_aliases"
    league: Mapped[str] = mapped_column(String(32))
    original: Mapped[str] = mapped_column(String(256))
    __table_args__ = (UniqueConstraint("league", "original", "source"),)


class MatchRow(Record, Base):
    __tablename__ = "market_matches"
    event_id: Mapped[str] = mapped_column(ForeignKey("canonical_sports_events.id"), index=True)
    first_market_id: Mapped[str] = mapped_column(ForeignKey("venue_markets.id"), index=True)
    second_market_id: Mapped[str] = mapped_column(ForeignKey("venue_markets.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    current: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        Index("ix_match_pair_current", "first_market_id", "second_market_id", "current"),
    )


class ReviewRow(Record, Base):
    __tablename__ = "market_match_reviews"
    match_id: Mapped[str] = mapped_column(ForeignKey("market_matches.id"), index=True)
    actor: Mapped[str] = mapped_column(String(64))


class CurrentBookRow(Record, Base):
    __tablename__ = "current_orderbooks"
    market_id: Mapped[str] = mapped_column(ForeignKey("venue_markets.id"), index=True)
    outcome: Mapped[str] = mapped_column(String(8))
    __table_args__ = (UniqueConstraint("market_id", "outcome", "source"),)


class SnapshotRow(Record, Base):
    __tablename__ = "orderbook_snapshots"
    market_id: Mapped[str] = mapped_column(ForeignKey("venue_markets.id"), index=True)
    outcome: Mapped[str] = mapped_column(String(8))


class OpportunityRow(Record, Base):
    __tablename__ = "arbitrage_opportunities"
    match_id: Mapped[str] = mapped_column(ForeignKey("market_matches.id"), index=True)
    event_id: Mapped[str] = mapped_column(String(64), index=True)
    league: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    net_profit: Mapped[Decimal] = mapped_column(Numeric(24, 8), index=True)
    net_return: Mapped[Decimal] = mapped_column(Numeric(24, 8), index=True)
    direction: Mapped[str] = mapped_column(String(8), index=True)


class ObservationRow(Record, Base):
    __tablename__ = "opportunity_observations"
    opportunity_id: Mapped[str] = mapped_column(String(160), index=True)


class RejectionRow(Record, Base):
    __tablename__ = "opportunity_rejections"
    match_id: Mapped[str] = mapped_column(String(160), index=True)
    reason: Mapped[str] = mapped_column(String(128), index=True)


class PaperTradeRow(Record, Base):
    __tablename__ = "paper_trades"
    opportunity_id: Mapped[str] = mapped_column(
        ForeignKey("arbitrage_opportunities.id"), unique=True
    )
    event_id: Mapped[str] = mapped_column(String(64), index=True)
    league: Mapped[str] = mapped_column(String(32), index=True)
    state: Mapped[str] = mapped_column(String(32), index=True)
    reserved_capital: Mapped[Decimal] = mapped_column(Numeric(24, 8))


class PaperLegRow(Record, Base):
    __tablename__ = "paper_trade_legs"
    trade_id: Mapped[str] = mapped_column(ForeignKey("paper_trades.id"), index=True)
    leg: Mapped[int] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("trade_id", "leg"),)


class RiskRow(Record, Base):
    __tablename__ = "risk_settings"
    revision: Mapped[int] = mapped_column(Integer, default=1)


class EligibilityRow(Record, Base):
    __tablename__ = "validation_eligibility"
    venue: Mapped[str] = mapped_column(String(32), index=True)
    __table_args__ = (UniqueConstraint("source", "venue"),)


class ValidationRow(Record, Base):
    __tablename__ = "candidate_validations"
    match_id: Mapped[str] = mapped_column(String(160), index=True)
    direction: Mapped[str] = mapped_column(String(8))


class EpisodeRow(Record, Base):
    __tablename__ = "validation_episodes"
    match_id: Mapped[str] = mapped_column(String(160), index=True)
    direction: Mapped[str] = mapped_column(String(8))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ShadowRow(Record, Base):
    __tablename__ = "shadow_trials"
    episode_id: Mapped[str] = mapped_column(String(160), index=True)
    state: Mapped[str] = mapped_column(String(32), index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ValidationConfigRow(Record, Base):
    __tablename__ = "validation_configuration"
    revision: Mapped[int] = mapped_column(Integer, default=1)


class RuleFamilyRow(Record, Base):
    __tablename__ = "validation_rule_families"


class BookMonitorRow(Record, Base):
    __tablename__ = "validation_book_monitoring"
    market_id: Mapped[str] = mapped_column(String(160), index=True)


class CoverageRow(Record, Base):
    __tablename__ = "validation_observation_coverage"
    match_id: Mapped[str] = mapped_column(String(160), index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)
    __table_args__ = (UniqueConstraint("source", "match_id", "day"),)


class AuditRow(Record, Base):
    __tablename__ = "audit_events"
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(128), index=True)


class HealthRow(Record, Base):
    __tablename__ = "venue_health"
    venue: Mapped[str] = mapped_column(String(32), unique=True)


class WorkerRow(Record, Base):
    __tablename__ = "worker_heartbeats"
    role: Mapped[str] = mapped_column(String(64), unique=True)
    owner: Mapped[str] = mapped_column(String(64))
    expires_epoch: Mapped[int] = mapped_column(Integer, default=0)


class AlertRow(Record, Base):
    __tablename__ = "alerts"
    severity: Mapped[str] = mapped_column(String(16))
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)


class SystemEventRow(Record, Base):
    __tablename__ = "system_events"
    role: Mapped[str] = mapped_column(String(64), index=True)


class AggregateRow(Record, Base):
    __tablename__ = "daily_statistics"
    day: Mapped[str] = mapped_column(String(10), index=True)


class ReplayRow(Record, Base):
    __tablename__ = "replay_runs"
    trade_id: Mapped[str | None] = mapped_column(ForeignKey("paper_trades.id"), nullable=True)


class SessionRow(Record, Base):
    __tablename__ = "admin_sessions"
    expires_epoch: Mapped[int] = mapped_column(Integer, index=True)


class AuthAttemptRow(Base):
    __tablename__ = "auth_rate_limits"
    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer)
    expires_epoch: Mapped[int] = mapped_column(Integer)


def create_database(settings: Settings) -> tuple[AsyncEngine, async_sessionmaker[Any]]:
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if not settings.async_database_url.startswith("sqlite"):
        kwargs.update(
            pool_size=5,
            max_overflow=5,
            pool_recycle=300,
            connect_args={
                "command_timeout": 60,
                "server_settings": {
                    "application_name": (
                        f"arbitrage:{settings.data_mode}:{settings.build_version[:12]}"
                    ),
                    "statement_timeout": "60000",
                    "transaction_timeout": "60000",
                    "lock_timeout": "10000",
                    "idle_in_transaction_session_timeout": "60000",
                },
            },
        )
    engine = create_async_engine(settings.async_database_url, **kwargs)
    if settings.async_database_url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def sqlite_pragmas(connection: Any, _: Any) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine, async_sessionmaker(engine, expire_on_commit=False)

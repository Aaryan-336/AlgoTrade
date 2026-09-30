"""SQLAlchemy tables (docs/design.md §3)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Stores UTC and always returns timezone-aware UTC (SQLite drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime cannot be stored")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


PRICE = Numeric(18, 4)
TS = UTCDateTime()


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}  # noqa: RUF012


class ConfigVersion(Base):
    __tablename__ = "config_versions"
    version: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(TS)
    risk: Mapped[dict[str, Any]] = mapped_column(JSON)
    strategy: Mapped[dict[str, Any]] = mapped_column(JSON)
    fingerprint: Mapped[str] = mapped_column(String(32))
    changed_by: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text, default="")


class BarRow(Base):
    __tablename__ = "bars"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "ts"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[str] = mapped_column(String(8))
    ts: Mapped[datetime] = mapped_column(TS, index=True)
    open: Mapped[Decimal] = mapped_column(PRICE)
    high: Mapped[Decimal] = mapped_column(PRICE)
    low: Mapped[Decimal] = mapped_column(PRICE)
    close: Mapped[Decimal] = mapped_column(PRICE)
    volume: Mapped[int] = mapped_column(BigInteger)


class NewsItemRow(Base):
    __tablename__ = "news_items"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # url hash
    source: Mapped[str] = mapped_column(String(64))
    publisher: Mapped[str] = mapped_column(String(128), default="")
    url: Mapped[str] = mapped_column(Text)
    headline: Mapped[str] = mapped_column(Text)
    snippet: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime] = mapped_column(TS, index=True)
    fetched_at: Mapped[datetime] = mapped_column(TS)
    symbols: Mapped[list[Any]] = mapped_column(JSON)
    credibility: Mapped[float] = mapped_column(Float)
    scored: Mapped[bool] = mapped_column(Boolean, default=False)


class SentimentRow(Base):
    __tablename__ = "sentiment"
    __table_args__ = (UniqueConstraint("news_id", "symbol"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    news_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    score: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    event_type: Mapped[str] = mapped_column(String(32))
    horizon: Mapped[str] = mapped_column(String(16))
    is_material: Mapped[bool] = mapped_column(Boolean)
    summary: Mapped[str] = mapped_column(String(240))
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(16))
    published_at: Mapped[datetime] = mapped_column(TS)
    credibility: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(TS)


class SignalRow(Base):
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(TS, index=True)
    bar_ts: Mapped[datetime] = mapped_column(TS)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    strategy: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(8))
    strength: Mapped[float] = mapped_column(Float)
    reasons: Mapped[list[Any]] = mapped_column(JSON)
    config_version: Mapped[int] = mapped_column(Integer)


class DecisionRow(Base):
    __tablename__ = "decisions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ts: Mapped[datetime] = mapped_column(TS, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    score: Mapped[float] = mapped_column(Float)
    components: Mapped[dict[str, Any]] = mapped_column(JSON)
    outcome: Mapped[str] = mapped_column(String(32))
    rejection_reason: Mapped[str] = mapped_column(Text, default="")
    config_version: Mapped[int] = mapped_column(Integer)


class OrderRow(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    mode: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(4))
    qty: Mapped[int] = mapped_column(Integer)
    order_type: Mapped[str] = mapped_column(String(8))
    limit_price: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    trigger_price: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    stop: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    target: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    state: Mapped[str] = mapped_column(String(20), index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    filled_qty: Mapped[int] = mapped_column(Integer, default=0)
    avg_fill_price: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    purpose: Mapped[str] = mapped_column(String(20))
    decision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    strategy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    parent_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TS, index=True)
    updated_at: Mapped[datetime] = mapped_column(TS)


class OrderEventRow(Base):
    __tablename__ = "order_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[datetime] = mapped_column(TS)
    from_state: Mapped[str] = mapped_column(String(20))
    to_state: Mapped[str] = mapped_column(String(20))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON)


class FillRow(Base):
    __tablename__ = "fills"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(64), index=True)
    broker_order_id: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(4))
    ts: Mapped[datetime] = mapped_column(TS, index=True)
    qty: Mapped[int] = mapped_column(Integer)
    price: Mapped[Decimal] = mapped_column(PRICE)
    charges: Mapped[dict[str, Any]] = mapped_column(JSON)
    charges_total: Mapped[Decimal] = mapped_column(PRICE)


class PositionRow(Base):
    __tablename__ = "positions"
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    qty: Mapped[int] = mapped_column(Integer)
    avg_price: Mapped[Decimal] = mapped_column(PRICE)
    stop: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    target: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    initial_stop: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    highest_close: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(TS)
    strategy: Mapped[str] = mapped_column(String(32))
    sector: Mapped[str] = mapped_column(String(64))


class RiskEventRow(Base):
    __tablename__ = "risk_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(TS, index=True)
    decision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    check_name: Mapped[str] = mapped_column(String(48))
    result: Mapped[str] = mapped_column(String(16))  # REJECT | BREAKER | INFO
    details: Mapped[dict[str, Any]] = mapped_column(JSON)


class AuditRow(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(TS, index=True)
    actor: Mapped[str] = mapped_column(String(32))
    event: Mapped[str] = mapped_column(String(64))
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    hash_prev: Mapped[str] = mapped_column(String(64))
    hash_self: Mapped[str] = mapped_column(String(64))


class EquitySnapshotRow(Base):
    __tablename__ = "equity_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(TS, index=True)
    equity: Mapped[Decimal] = mapped_column(PRICE)
    cash: Mapped[Decimal] = mapped_column(PRICE)
    positions_value: Mapped[Decimal] = mapped_column(PRICE)
    realized: Mapped[Decimal] = mapped_column(PRICE)
    fees: Mapped[Decimal] = mapped_column(PRICE)
    drawdown_pct: Mapped[float] = mapped_column(Float)


class SystemStateRow(Base):
    __tablename__ = "system_state"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(TS)


class BacktestRunRow(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(TS)
    params: Mapped[dict[str, Any]] = mapped_column(JSON)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON)
    equity_curve: Mapped[list[Any]] = mapped_column(JSON)
    trades: Mapped[list[Any]] = mapped_column(JSON)

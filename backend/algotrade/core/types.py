"""Domain types shared across the system.

Money and prices are ``Decimal``; quantities are ``int``; timestamps are
timezone-aware (UTC for storage, Asia/Kolkata for display).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal


class Mode(StrEnum):
    PAPER = "PAPER"
    LIVE_APPROVAL = "LIVE_APPROVAL"
    LIVE_LIMITED = "LIVE_LIMITED"
    LIVE = "LIVE"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"  # stop-market (SL-M): becomes a market order once triggered


class OrderState(StrEnum):
    CREATED = "CREATED"
    RISK_APPROVED = "RISK_APPROVED"
    REJECTED = "REJECTED"
    SUBMITTED = "SUBMITTED"
    FAILED = "FAILED"
    ACKED = "ACKED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_REQ = "CANCEL_REQ"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class IntentKind(StrEnum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"


Action = Literal["BUY", "SELL", "HOLD"]


@dataclass(frozen=True, slots=True)
class Instrument:
    symbol: str
    name: str
    sector: str
    exchange: str = "NSE"
    isin: str | None = None
    tick_size: Decimal = Decimal("0.05")
    lot_size: int = 1
    aliases: tuple[str, ...] = ()
    is_active: bool = True


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: str
    timeframe: str  # "1d" or "15m"
    ts: datetime  # bar open time, UTC
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True, slots=True)
class Tick:
    symbol: str
    ts: datetime
    ltp: Decimal
    volume: int | None = None


@dataclass(frozen=True, slots=True)
class Signal:
    symbol: str
    strategy: str
    action: Action
    strength: float  # 0..1
    reasons: tuple[str, ...]
    suggested_stop: Decimal | None = None
    suggested_target: Decimal | None = None


@dataclass(slots=True)
class OrderIntent:
    """What the Decision Engine wants. It carries no authority: the Risk
    Engine decides whether it becomes an order, and at what size."""

    decision_id: str
    symbol: str
    side: Side
    kind: IntentKind
    strategy: str
    reason: str
    ref_price: Decimal
    bar_ts: datetime
    created_at: datetime
    expires_at: datetime
    stop: Decimal | None = None
    target: Decimal | None = None
    score: float = 0.0
    size_multiplier: float = 1.0  # regime/sentiment modifier, never above 1.25


@dataclass(slots=True)
class Order:
    id: str
    idempotency_key: str
    mode: Mode
    symbol: str
    side: Side
    qty: int
    order_type: OrderType
    state: OrderState
    created_at: datetime
    updated_at: datetime
    purpose: str  # entry | exit | protective_stop
    decision_id: str | None = None
    strategy: str | None = None
    limit_price: Decimal | None = None
    trigger_price: Decimal | None = None
    stop: Decimal | None = None
    target: Decimal | None = None
    broker_order_id: str | None = None
    filled_qty: int = 0
    avg_fill_price: Decimal | None = None
    reason: str = ""
    parent_order_id: str | None = None

    @property
    def remaining_qty(self) -> int:
        return self.qty - self.filled_qty


@dataclass(frozen=True, slots=True)
class Charges:
    brokerage: Decimal = Decimal(0)
    stt: Decimal = Decimal(0)
    exchange: Decimal = Decimal(0)
    sebi: Decimal = Decimal(0)
    stamp_duty: Decimal = Decimal(0)
    gst: Decimal = Decimal(0)
    dp: Decimal = Decimal(0)

    @property
    def total(self) -> Decimal:
        return (
            self.brokerage + self.stt + self.exchange + self.sebi
            + self.stamp_duty + self.gst + self.dp
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "brokerage": str(self.brokerage),
            "stt": str(self.stt),
            "exchange": str(self.exchange),
            "sebi": str(self.sebi),
            "stamp_duty": str(self.stamp_duty),
            "gst": str(self.gst),
            "dp": str(self.dp),
            "total": str(self.total),
        }


@dataclass(frozen=True, slots=True)
class Fill:
    order_id: str
    broker_order_id: str
    symbol: str
    side: Side
    ts: datetime
    qty: int
    price: Decimal
    charges: Charges


@dataclass(slots=True)
class Position:
    symbol: str
    qty: int
    avg_price: Decimal
    opened_at: datetime
    strategy: str
    sector: str
    stop: Decimal | None = None
    target: Decimal | None = None
    initial_stop: Decimal | None = None
    highest_close: Decimal | None = None
    realized_pnl: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)

    @property
    def cost_basis(self) -> Decimal:
        return self.avg_price * self.qty


@dataclass(frozen=True, slots=True)
class BrokerAck:
    broker_order_id: str
    accepted: bool
    message: str = ""


@dataclass(frozen=True, slots=True)
class BrokerPosition:
    symbol: str
    qty: int
    avg_price: Decimal


@dataclass(frozen=True, slots=True)
class Funds:
    cash: Decimal


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    name: str
    connected: bool
    last_update: datetime | None
    message: str = ""


@dataclass(slots=True)
class SentimentItem:
    news_id: str
    symbol: str
    sentiment: float
    confidence: float
    event_type: str
    horizon: str
    is_material: bool
    summary: str
    published_at: datetime
    credibility: float


@dataclass(slots=True)
class SentimentAggregate:
    symbol: str
    score: float  # -1..1, decayed and confidence weighted; 0 when missing
    n_items: int
    negative_material: bool
    missing: bool
    latest: datetime | None = None
    reasons: list[str] = field(default_factory=list)

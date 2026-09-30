"""Circuit breakers (docs/risk-management.md §5).

A tripped breaker either blocks new entries or halts all engine orders.
Only ``stale_data`` and (optionally) ``daily_loss`` clear on their own; the
rest need a manual reset with a written note (rule B-7).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from algotrade.config.models import RiskConfig


class Severity(StrEnum):
    BLOCK_ENTRIES = "block_entries"
    HALT = "halt"


BREAKERS: dict[str, Severity] = {
    "daily_loss": Severity.BLOCK_ENTRIES,
    "weekly_loss": Severity.BLOCK_ENTRIES,
    "drawdown": Severity.HALT,
    "stale_data": Severity.BLOCK_ENTRIES,
    "data_quarantine": Severity.BLOCK_ENTRIES,
    "broker_error": Severity.HALT,
    "reject_storm": Severity.HALT,
    "reconciliation": Severity.HALT,
    "unhandled_exception": Severity.HALT,
}

AUTO_RESET = {"stale_data"}


@dataclass
class TrippedBreaker:
    name: str
    reason: str
    tripped_at: datetime

    @property
    def severity(self) -> Severity:
        return BREAKERS[self.name]


class BreakerBoard:
    """In-memory view of breaker state; the engine persists it after changes."""

    def __init__(self, tripped: dict[str, TrippedBreaker] | None = None) -> None:
        self.tripped: dict[str, TrippedBreaker] = dict(tripped or {})

    def trip(self, name: str, reason: str, ts: datetime) -> bool:
        if name not in BREAKERS:
            raise KeyError(name)
        if name in self.tripped:
            return False
        self.tripped[name] = TrippedBreaker(name, reason, ts)
        return True

    def reset(self, name: str) -> bool:
        return self.tripped.pop(name, None) is not None

    @property
    def halted(self) -> bool:
        return any(b.severity is Severity.HALT for b in self.tripped.values())

    @property
    def entries_blocked(self) -> bool:
        return bool(self.tripped)

    def reasons(self) -> list[str]:
        return [f"{b.name}: {b.reason}" for b in self.tripped.values()]

    def to_state(self) -> dict[str, Any]:
        return {n: {"reason": b.reason, "tripped_at": b.tripped_at.isoformat(),
                    "severity": b.severity.value} for n, b in self.tripped.items()}

    @classmethod
    def from_state(cls, state: dict[str, Any] | None) -> BreakerBoard:
        out: dict[str, TrippedBreaker] = {}
        for name, v in (state or {}).items():
            if name in BREAKERS:
                out[name] = TrippedBreaker(name, str(v.get("reason", "")),
                                           datetime.fromisoformat(v["tripped_at"]))
        return cls(out)


def loss_pct(start: Decimal, now: Decimal) -> float:
    if start <= 0:
        return 0.0
    return float((start - now) / start * 100)


def evaluate_loss_breakers(equity: Decimal, day_start: Decimal, week_start: Decimal,
                           peak: Decimal, cfg: RiskConfig) -> list[tuple[str, str]]:
    """Return (breaker, reason) for each loss limit currently exceeded."""
    out: list[tuple[str, str]] = []
    d = loss_pct(day_start, equity)
    w = loss_pct(week_start, equity)
    dd = loss_pct(peak, equity)
    if d >= cfg.max_daily_loss_pct:
        out.append(("daily_loss", f"day loss {d:.2f}% >= {cfg.max_daily_loss_pct}%"))
    if w >= cfg.max_weekly_loss_pct:
        out.append(("weekly_loss", f"week loss {w:.2f}% >= {cfg.max_weekly_loss_pct}%"))
    if dd >= cfg.max_drawdown_pct:
        out.append(("drawdown", f"drawdown {dd:.2f}% >= {cfg.max_drawdown_pct}%"))
    return out

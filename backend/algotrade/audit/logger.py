"""Append-only, hash-chained audit log (docs/design.md §3, rule I-11).

Each row stores ``hash_self = sha256(hash_prev + canonical(row))`` so any
edit or deletion breaks the chain and ``verify`` reports where.
"""

from __future__ import annotations

import hashlib
import threading
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from algotrade.core.serde import canonical_json, to_jsonable
from algotrade.db.models import AuditRow
from algotrade.db.session import Database

GENESIS = "0" * 64


def _digest(prev: str, ts: datetime, actor: str, event: str, correlation_id: str | None,
            config_version: int | None, payload: dict[str, Any]) -> str:
    body = canonical_json({
        "ts": ts, "actor": actor, "event": event, "correlation_id": correlation_id,
        "config_version": config_version, "payload": payload,
    })
    return hashlib.sha256((prev + body).encode()).hexdigest()


class AuditLog:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._lock = threading.Lock()

    def record(self, ts: datetime, actor: str, event: str, payload: dict[str, Any],
               correlation_id: str | None = None, config_version: int | None = None) -> str:
        ts = ts.astimezone(UTC)
        clean = to_jsonable(payload)
        with self._lock, self.db.session() as s:
            last = s.execute(select(AuditRow.hash_self).order_by(AuditRow.id.desc()).limit(1))
            prev = last.scalar_one_or_none() or GENESIS
            h = _digest(prev, ts, actor, event, correlation_id, config_version, clean)
            s.add(AuditRow(ts=ts, actor=actor, event=event, correlation_id=correlation_id,
                           config_version=config_version, payload=clean, hash_prev=prev,
                           hash_self=h))
        return h

    def verify(self) -> tuple[bool, int | None]:
        """Returns (ok, first_bad_row_id)."""
        prev = GENESIS
        with self.db.session() as s:
            for row in s.execute(select(AuditRow).order_by(AuditRow.id)).scalars():
                expected = _digest(prev, row.ts, row.actor, row.event, row.correlation_id,
                                   row.config_version, row.payload)
                if row.hash_prev != prev or row.hash_self != expected:
                    return False, row.id
                prev = row.hash_self
        return True, None

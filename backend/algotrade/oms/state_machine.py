"""Order state machine (docs/design.md §4). Only listed transitions exist;
anything else raises, and the engine halts on it."""

from __future__ import annotations

from algotrade.core.types import OrderState as S


class IllegalTransition(RuntimeError):
    pass


TRANSITIONS: dict[S, frozenset[S]] = {
    S.CREATED: frozenset({S.RISK_APPROVED, S.REJECTED}),
    S.RISK_APPROVED: frozenset({S.SUBMITTED, S.REJECTED}),
    S.SUBMITTED: frozenset({S.ACKED, S.FAILED}),
    S.ACKED: frozenset({S.PARTIALLY_FILLED, S.FILLED, S.CANCEL_REQ, S.EXPIRED}),
    S.PARTIALLY_FILLED: frozenset({S.PARTIALLY_FILLED, S.FILLED, S.CANCEL_REQ}),
    # A fill can race a cancel request; the broker's answer wins.
    S.CANCEL_REQ: frozenset({S.CANCELLED, S.EXPIRED, S.FILLED, S.PARTIALLY_FILLED}),
    S.REJECTED: frozenset(),
    S.FAILED: frozenset(),
    S.FILLED: frozenset(),
    S.CANCELLED: frozenset(),
    S.EXPIRED: frozenset(),
}

TERMINAL = frozenset(s for s, nxt in TRANSITIONS.items() if not nxt)
WORKING = frozenset({S.SUBMITTED, S.ACKED, S.PARTIALLY_FILLED, S.CANCEL_REQ})


def check_transition(frm: S, to: S) -> None:
    if to not in TRANSITIONS[frm]:
        raise IllegalTransition(f"{frm.value} -> {to.value}")

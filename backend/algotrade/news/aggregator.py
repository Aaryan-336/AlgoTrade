"""Turns scored news into one per-symbol sentiment input
(docs/strategy-spec.md §5). Missing or stale sentiment is neutral, never
positive."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta

from algotrade.config.models import SentimentConfig
from algotrade.core.types import SentimentAggregate, SentimentItem

HORIZON_HALF_LIFE_MULT = {"intraday": 0.35, "days": 1.0, "weeks": 3.0}
MAX_SINGLE_ITEM_SHARE = 0.6  # one headline cannot dominate a thin sample


def aggregate(symbol: str, items: Iterable[SentimentItem], now: datetime,
              cfg: SentimentConfig,
              move_since: Callable[[datetime], float | None] | None = None) -> SentimentAggregate:
    """``move_since(ts)`` returns the % price move since ``ts`` (for novelty)."""
    since = now - timedelta(days=cfg.lookback_days)
    veto_since = now - timedelta(days=cfg.veto_window_days)
    weighted: list[tuple[float, float]] = []
    negative_material = False
    reasons: list[str] = []
    latest: datetime | None = None
    n = 0
    for it in items:
        if it.symbol != symbol or it.published_at < since or it.confidence < cfg.min_confidence:
            continue
        n += 1
        latest = max(latest, it.published_at) if latest else it.published_at
        age_days = max(0.0, (now - it.published_at).total_seconds() / 86400)
        half_life = cfg.half_life_days * HORIZON_HALF_LIFE_MULT.get(it.horizon, 1.0)
        decay = math.pow(0.5, age_days / half_life)
        w = it.confidence * it.credibility * decay
        if move_since is not None:
            move = move_since(it.published_at)
            # Price already moved in the news' direction: the edge may be priced in.
            if move is not None and abs(move) >= cfg.novelty_move_pct and move * it.sentiment > 0:
                w *= 0.5
        weighted.append((it.sentiment, w))
        if (it.is_material and it.sentiment <= cfg.veto_score
                and it.published_at >= veto_since and it.credibility >= 0.5):
            negative_material = True
            reasons.append(f"negative material news: {it.summary[:120]}")
    total_w = sum(w for _, w in weighted)
    if total_w <= 0:
        return SentimentAggregate(symbol, 0.0, n, negative_material, True, latest, reasons)
    cap = total_w * MAX_SINGLE_ITEM_SHARE if len(weighted) > 1 else total_w
    capped = [(s, min(w, cap)) for s, w in weighted]
    cw = sum(w for _, w in capped)
    score = sum(s * w for s, w in capped) / cw
    # Thin evidence shrinks toward neutral.
    shrink = min(1.0, total_w / 1.5)
    score = max(-1.0, min(1.0, score * shrink))
    return SentimentAggregate(symbol, score, n, negative_material, False, latest, reasons)

"""Score components (docs/design.md §6). Each component is in [0, 1];
sentiment maps -1..1 to 0..1 with missing sentiment at the neutral 0.5."""

from __future__ import annotations

from dataclasses import dataclass

from algotrade.config.models import StrategyConfig
from algotrade.core.types import SentimentAggregate
from algotrade.features.indicators import FeatureSet, is_valid


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


@dataclass(frozen=True, slots=True)
class Components:
    trend: float
    momentum: float
    volume: float
    sentiment: float
    score: float

    def as_dict(self) -> dict[str, float]:
        return {"trend": round(self.trend, 3), "momentum": round(self.momentum, 3),
                "volume": round(self.volume, 3), "sentiment": round(self.sentiment, 3),
                "score": round(self.score, 3)}


def components(fs: FeatureSet, sent: SentimentAggregate | None, cfg: StrategyConfig) -> Components:
    tp = cfg.strategies.trend
    close = fs.close
    fast, slow = fs.ema(tp.fast_ema)[-1], fs.ema(tp.slow_ema)[-1]
    r = fs.rsi(tp.rsi_period)[-1]
    vavg = fs.volume_sma(tp.volume_period)[-1]

    trend = 0.0
    if is_valid(slow) and is_valid(fast) and slow > 0:
        trend = (0.4 * (close > slow) + 0.3 * (fast > slow)
                 + 0.3 * _clamp((fast / slow - 1) / 0.05))
    momentum = 0.0
    if is_valid(r):
        momentum = 0.0 if r >= 80 else _clamp(1 - abs(r - 60) / 30)
    volume = _clamp(fs.volumes[-1] / vavg / 2) if is_valid(vavg) and vavg > 0 else 0.0
    use_sent = cfg.sentiment.enabled and sent is not None and not sent.missing
    sentiment = (sent.score + 1) / 2 if use_sent and sent else 0.5

    w = cfg.decision.weights
    total = w.trend + w.momentum + w.volume + w.sentiment
    score = (w.trend * trend + w.momentum * momentum + w.volume * volume
             + w.sentiment * sentiment) / total
    return Components(trend, momentum, volume, sentiment, score)

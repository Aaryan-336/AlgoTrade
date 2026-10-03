"""Decision Engine (docs/design.md §6, docs/strategy-spec.md §4-8).

Pure: combines strategy signals, score components, sentiment, regime and
portfolio context into order *intents*. It never sizes beyond a hint and
never touches a broker; the Risk Engine has the final say.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from algotrade.config.models import StrategyConfig
from algotrade.core.clock import MarketCalendar, to_ist
from algotrade.core.types import (
    IntentKind,
    OrderIntent,
    Position,
    SentimentAggregate,
    Side,
    Signal,
)
from algotrade.decision.scoring import Components, components
from algotrade.features.indicators import FeatureSet, is_valid
from algotrade.strategies.base import Strategy, StrategyContext
from algotrade.strategies.registry import max_holding_days


@dataclass(slots=True)
class DecisionRecord:
    decision_id: str
    symbol: str
    kind: str  # entry | exit | hold | skip
    score: float
    components: dict[str, object]
    outcome: str
    reason: str = ""


@dataclass(slots=True)
class CycleResult:
    intents: list[OrderIntent] = field(default_factory=list)
    decisions: list[DecisionRecord] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    stop_updates: dict[str, Decimal] = field(default_factory=dict)
    highest_close: dict[str, Decimal] = field(default_factory=dict)
    regime: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class _Candidate:
    symbol: str
    close: Decimal
    signal: Signal
    comps: Components
    size_multiplier: float


def decision_id(bar_ts: datetime, symbol: str, kind: str) -> str:
    return f"D{to_ist(bar_ts):%Y%m%d%H%M}-{symbol}-{kind}"


class DecisionEngine:
    def __init__(self, cfg: StrategyConfig, strategies: list[Strategy],
                 calendar: MarketCalendar, max_open_positions: int) -> None:
        self.cfg = cfg
        self.strategies = strategies
        self.calendar = calendar
        self.max_open_positions = max_open_positions

    # ----------------------------------------------------------------- API
    def run(self, now: datetime, bar_ts: datetime, features: Mapping[str, FeatureSet],
            positions: Mapping[str, Position], sentiment: Mapping[str, SentimentAggregate],
            index_fs: FeatureSet | None, vix: float | None,
            expires_at: datetime) -> CycleResult:
        cfg = self.cfg
        res = CycleResult()
        regime_block, size_mult = self._regime(index_fs, vix, res)
        today = to_ist(now).date()
        holdings_scores: dict[str, float] = {}
        exiting: set[str] = set()
        candidates: list[_Candidate] = []
        closes: dict[str, Decimal] = {}

        for sym, fs in features.items():
            if len(fs) == 0:
                continue
            closes[sym] = Decimal(str(fs.close))
            pos = positions.get(sym)
            sent = sentiment.get(sym)
            ctx = StrategyContext(now=now, holding=pos is not None,
                                  entry_price=pos.avg_price if pos else None)
            sigs = [s.evaluate(sym, fs, ctx) for s in self.strategies]
            res.signals.extend(s for s in sigs if s.action != "HOLD")
            if len(fs) < max(cfg.universe.min_history_bars, 30):
                continue
            comps = components(fs, sent, cfg)
            cdict: dict[str, object] = {**comps.as_dict(),
                                        "sentiment_raw": sent.score if sent else None,
                                        "sentiment_items": sent.n_items if sent else 0}
            if pos is not None:
                intent = self._manage_position(now, bar_ts, fs, pos, sigs, comps, sent, today,
                                               expires_at, res, cdict)
                holdings_scores[sym] = comps.score
                if intent:
                    exiting.add(sym)
                    res.intents.append(intent)
                continue

            buys = [s for s in sigs if s.action == "BUY"]
            if not buys:
                continue
            best = max(buys, key=lambda s: s.strength)
            cdict["signal"] = {"strategy": best.strategy, "strength": round(best.strength, 3),
                               "reasons": list(best.reasons)}
            veto = self._vetoes(sym, fs, sent, today, regime_block)
            did = decision_id(bar_ts, sym, "entry")
            if veto:
                res.decisions.append(DecisionRecord(did, sym, "entry", comps.score, cdict,
                                                    "vetoed", veto))
                continue
            if comps.score < cfg.decision.entry_threshold:
                res.decisions.append(DecisionRecord(
                    did, sym, "entry", comps.score, cdict, "below_threshold",
                    f"score {comps.score:.2f} < {cfg.decision.entry_threshold}"))
                continue
            mult = size_mult * (1.1 if sent and not sent.missing and sent.score >= 0.3 else 1.0)
            candidates.append(_Candidate(sym, Decimal(str(fs.close)), best, comps, mult))

        self._allocate(now, bar_ts, candidates, positions, holdings_scores, exiting, today,
                       expires_at, res, closes)
        return res

    def rank(self, now: datetime, features: Mapping[str, FeatureSet],
             positions: Mapping[str, Position], sentiment: Mapping[str, SentimentAggregate],
             index_fs: FeatureSet | None, vix: float | None) -> list[dict[str, object]]:
        """Score every symbol for the watchlist view. Read-only: no intents."""
        cfg = self.cfg
        regime_block, _ = self._regime(index_fs, vix, CycleResult())
        today = to_ist(now).date()
        out: list[dict[str, object]] = []
        for sym, fs in features.items():
            if len(fs) == 0:
                continue
            pos = positions.get(sym)
            sent = sentiment.get(sym)
            row: dict[str, object] = {"symbol": sym, "close": fs.close, "held": pos is not None,
                                      "bar_ts": fs.last.ts}
            if len(fs) < max(cfg.universe.min_history_bars, 30):
                out.append({**row, "status": "not_enough_history", "score": None,
                            "reason": f"only {len(fs)} days of history"})
                continue
            ctx = StrategyContext(now=now, holding=pos is not None,
                                  entry_price=pos.avg_price if pos else None)
            sigs = [s.evaluate(sym, fs, ctx) for s in self.strategies]
            comps = components(fs, sent, cfg)
            buys = [s for s in sigs if s.action == "BUY"]
            sells = [s for s in sigs if s.action == "SELL"]
            row.update(comps.as_dict())
            row["sentiment_raw"] = sent.score if sent and not sent.missing else None
            row["change_pct"] = ((fs.closes[-1] / fs.closes[-2] - 1) * 100
                                 if len(fs) > 1 and fs.closes[-2] else None)
            if pos is not None:
                status, reason = "holding", (f"exit signal: {'; '.join(sells[0].reasons)}"
                                             if sells else "trend intact")
            elif not buys:
                best_hold = max(sigs, key=lambda s: len(s.reasons), default=None)
                status = "no_signal"
                reason = "; ".join(best_hold.reasons[:2]) if best_hold else "no signal"
            else:
                veto = self._vetoes(sym, fs, sent, today, regime_block)
                if veto:
                    status, reason = "vetoed", veto
                elif comps.score < cfg.decision.entry_threshold:
                    status = "below_threshold"
                    reason = f"score {comps.score:.2f} < {cfg.decision.entry_threshold}"
                else:
                    best = max(buys, key=lambda s: s.strength)
                    status, reason = "buy_candidate", f"{best.strategy}: {'; '.join(best.reasons)}"
                    row["stop"] = best.suggested_stop
                    row["target"] = best.suggested_target
                    row["strategy"] = best.strategy
            out.append({**row, "status": status, "reason": reason})
        out.sort(key=lambda r: (r.get("score") is None, -float(r.get("score") or 0)))  # type: ignore[arg-type]
        return out

    # -------------------------------------------------------------- regime
    def _regime(self, index_fs: FeatureSet | None, vix: float | None,
                res: CycleResult) -> tuple[str, float]:
        rc = self.cfg.regime
        size_mult = 1.0
        if not rc.enabled:
            res.regime = {"enabled": False}
            return "", size_mult
        block = ""
        if index_fs is None or len(index_fs) < rc.index_ema:
            block = "regime unknown: no index history (fail closed)"
        else:
            ema_v = index_fs.ema(rc.index_ema)[-1]
            if not is_valid(ema_v):
                block = "regime unknown: index EMA not ready"
            elif index_fs.close < ema_v:
                block = f"index below EMA{rc.index_ema} ({index_fs.close:.0f} < {ema_v:.0f})"
            res.regime = {"index_close": round(index_fs.close, 2),
                          "index_ema": round(ema_v, 2) if is_valid(ema_v) else None}
        if vix is not None and vix > rc.vix_reduce_above:
            size_mult = 0.5
        res.regime.update({"enabled": True, "entries_blocked": bool(block), "reason": block,
                           "vix": vix, "size_multiplier": size_mult})
        return block, size_mult

    # --------------------------------------------------------------- vetoes
    def _vetoes(self, sym: str, fs: FeatureSet, sent: SentimentAggregate | None, today: date,
                regime_block: str) -> str:
        cfg = self.cfg
        if regime_block:
            return regime_block
        if fs.close < cfg.universe.min_price:
            return f"price below {cfg.universe.min_price}"
        atv = fs.avg_traded_value(20)
        if atv < cfg.universe.min_avg_traded_value:
            return f"illiquid: avg traded value {atv / 1e7:.1f} Cr"
        if cfg.sentiment.enabled and sent is not None and sent.negative_material:
            return "negative material news"
        if today in cfg.regime.event_blackout_dates:
            return "market event blackout"
        if today in cfg.regime.symbol_blackouts.get(sym, []):
            return "symbol event blackout (e.g. results)"
        return ""

    # ----------------------------------------------------- position exits
    def _manage_position(self, now: datetime, bar_ts: datetime, fs: FeatureSet, pos: Position,
                         sigs: list[Signal], comps: Components, sent: SentimentAggregate | None,
                         today: date, expires_at: datetime, res: CycleResult,
                         cdict: dict[str, object]) -> OrderIntent | None:
        cfg = self.cfg
        close = Decimal(str(fs.close))
        reasons: list[str] = []
        own = [s for s in sigs if s.action == "SELL" and s.strategy == pos.strategy]
        if own:
            reasons.append(f"{own[0].strategy} exit: {'; '.join(own[0].reasons)}")
        if comps.score <= cfg.decision.exit_threshold:
            reasons.append(f"score {comps.score:.2f} <= exit threshold "
                           f"{cfg.decision.exit_threshold}")
        if (cfg.sentiment.enabled and cfg.sentiment.exit_on_negative_material and sent
                and sent.negative_material):
            reasons.append("negative material news")
        held = self.calendar.trading_days_between(to_ist(pos.opened_at).date(), today)
        max_days = max_holding_days(cfg.strategies, pos.strategy)
        if held >= max_days:
            reasons.append(f"time stop: held {held} >= {max_days} trading days")
        if pos.target is not None and close >= pos.target:
            reasons.append(f"target {pos.target} reached")
        if pos.stop is not None and close <= pos.stop:
            reasons.append(f"close {close} at or below stop {pos.stop}")

        did = decision_id(bar_ts, pos.symbol, "exit")
        if reasons:
            res.decisions.append(DecisionRecord(did, pos.symbol, "exit", comps.score, cdict,
                                                "intent", "; ".join(reasons)))
            return OrderIntent(did, pos.symbol, Side.SELL, IntentKind.EXIT, pos.strategy,
                               "; ".join(reasons), close, bar_ts, now, expires_at,
                               score=comps.score)

        # Trailing stop: only ever moves up, and only after trailing_after_r.
        highest = max(pos.highest_close or close, close)
        res.highest_close[pos.symbol] = highest
        atr_v = fs.atr(cfg.strategies.trend.atr_period)[-1]
        init = pos.initial_stop or pos.stop
        if init is not None and is_valid(atr_v) and pos.avg_price > init:
            r_unit = pos.avg_price - init
            if highest - pos.avg_price >= r_unit * Decimal(str(cfg.decision.trailing_after_r)):
                trail = highest - Decimal(str(round(cfg.decision.trailing_atr_mult * atr_v, 2)))
                current = pos.stop or init
                if trail > current and trail < close:
                    res.stop_updates[pos.symbol] = trail.quantize(Decimal("0.05"))
        return None

    # ------------------------------------------------ entries and rotation
    def _allocate(self, now: datetime, bar_ts: datetime, candidates: list[_Candidate],
                  positions: Mapping[str, Position], holdings_scores: dict[str, float],
                  exiting: set[str], today: date, expires_at: datetime,
                  res: CycleResult, closes: Mapping[str, Decimal]) -> None:
        cfg = self.cfg
        candidates.sort(key=lambda c: c.comps.score, reverse=True)
        slots = self.max_open_positions - (len(positions) - len(exiting))
        remaining: list[_Candidate] = []
        for c in candidates:
            if slots > 0:
                res.intents.append(self._entry(now, bar_ts, c, expires_at, "entry signal"))
                res.decisions.append(DecisionRecord(
                    decision_id(bar_ts, c.symbol, "entry"), c.symbol, "entry", c.comps.score,
                    self._cdict(c), "intent", f"{c.signal.strategy} buy"))
                slots -= 1
            else:
                remaining.append(c)

        rot = cfg.rotation
        if rot.enabled and remaining:
            eligible = {
                s: sc for s, sc in holdings_scores.items()
                if s not in exiting and self.calendar.trading_days_between(
                    to_ist(positions[s].opened_at).date(), today) >= rot.min_hold_days
            }
            if eligible:
                weakest = min(eligible, key=lambda s: eligible[s])
                best = remaining[0]
                gap = best.comps.score - eligible[weakest]
                if gap >= rot.min_score_gap:
                    pos = positions[weakest]
                    reason = (f"rotation: {best.symbol} scores {best.comps.score:.2f} vs "
                              f"{weakest} {eligible[weakest]:.2f}")
                    did = decision_id(bar_ts, weakest, "exit")
                    res.intents.append(OrderIntent(
                        did, weakest, Side.SELL, IntentKind.EXIT, pos.strategy, reason,
                        closes.get(weakest, pos.avg_price), bar_ts, now, expires_at,
                        score=eligible[weakest]))
                    res.decisions.append(DecisionRecord(did, weakest, "exit", eligible[weakest],
                                                        {"rotation_to": best.symbol}, "intent",
                                                        reason))
                    res.intents.append(self._entry(now, bar_ts, best, expires_at, reason))
                    res.decisions.append(DecisionRecord(
                        decision_id(bar_ts, best.symbol, "entry"), best.symbol, "entry",
                        best.comps.score, self._cdict(best), "intent", reason))
                    remaining = remaining[1:]
        for c in remaining:
            res.decisions.append(DecisionRecord(
                decision_id(bar_ts, c.symbol, "entry"), c.symbol, "entry", c.comps.score,
                self._cdict(c), "no_slot", "max open positions reached"))

    @staticmethod
    def _cdict(c: _Candidate) -> dict[str, object]:
        return {**c.comps.as_dict(), "signal": {"strategy": c.signal.strategy,
                                                "strength": round(c.signal.strength, 3),
                                                "reasons": list(c.signal.reasons)},
                "size_multiplier": c.size_multiplier}

    @staticmethod
    def _entry(now: datetime, bar_ts: datetime, c: _Candidate, expires_at: datetime,
               reason: str) -> OrderIntent:
        return OrderIntent(
            decision_id(bar_ts, c.symbol, "entry"), c.symbol, Side.BUY, IntentKind.ENTRY,
            c.signal.strategy, reason, c.close, bar_ts, now, expires_at,
            stop=c.signal.suggested_stop, target=c.signal.suggested_target,
            score=c.comps.score, size_multiplier=c.size_multiplier)


def expiry_for(bar_ts: datetime, timeframe: str, ttl_bars: int,
               calendar: MarketCalendar) -> datetime:
    """Daily intents live until the close of the next ``ttl_bars`` sessions;
    15-minute intents for ``ttl_bars`` bars."""
    if timeframe == "15m":
        return bar_ts + timedelta(minutes=15 * (ttl_bars + 1))
    d = to_ist(bar_ts).date()
    for _ in range(ttl_bars):
        d = calendar.next_trading_day(d)
    return calendar.session_bounds(d)[1]

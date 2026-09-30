"""The single repository layer for database access."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select

from algotrade.config.models import AppConfig, RiskConfig, StrategyConfig
from algotrade.core.serde import to_jsonable
from algotrade.core.types import Bar, Fill, Order, OrderState, Position, SentimentItem, Signal
from algotrade.db.models import (
    BacktestRunRow,
    BarRow,
    ConfigVersion,
    DecisionRow,
    EquitySnapshotRow,
    FillRow,
    NewsItemRow,
    OrderEventRow,
    OrderRow,
    PositionRow,
    RiskEventRow,
    SentimentRow,
    SignalRow,
    SystemStateRow,
)
from algotrade.db.session import Database


class Repository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ---------------------------------------------------------------- state
    def get_state(self, key: str) -> dict[str, Any] | None:
        with self.db.session() as s:
            row = s.get(SystemStateRow, key)
            return dict(row.value) if row else None

    def set_state(self, key: str, value: dict[str, Any], ts: datetime) -> None:
        with self.db.session() as s:
            row = s.get(SystemStateRow, key)
            if row is None:
                s.add(SystemStateRow(key=key, value=to_jsonable(value), updated_at=ts))
            else:
                row.value = to_jsonable(value)
                row.updated_at = ts

    def state_updated_at(self, key: str) -> datetime | None:
        with self.db.session() as s:
            row = s.get(SystemStateRow, key)
            return row.updated_at if row else None

    # --------------------------------------------------------------- config
    def latest_config(self) -> tuple[int, AppConfig] | None:
        with self.db.session() as s:
            row = s.execute(
                select(ConfigVersion).order_by(ConfigVersion.version.desc()).limit(1)
            ).scalar_one_or_none()
            if row is None:
                return None
            cfg = AppConfig(risk=RiskConfig.model_validate(row.risk),
                            strategy=StrategyConfig.model_validate(row.strategy))
            return row.version, cfg

    def save_config(self, cfg: AppConfig, ts: datetime, changed_by: str, reason: str) -> int:
        with self.db.session() as s:
            row = ConfigVersion(ts=ts, risk=cfg.risk.model_dump(mode="json"),
                                strategy=cfg.strategy.model_dump(mode="json"),
                                fingerprint=cfg.fingerprint(), changed_by=changed_by,
                                reason=reason)
            s.add(row)
            s.flush()
            return row.version

    def config_history(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.db.session() as s:
            rows = s.execute(select(ConfigVersion).order_by(ConfigVersion.version.desc())
                             .limit(limit)).scalars()
            return [{"version": r.version, "ts": r.ts, "fingerprint": r.fingerprint,
                     "changed_by": r.changed_by, "reason": r.reason} for r in rows]

    # ----------------------------------------------------------------- bars
    def upsert_bars(self, bars: Iterable[Bar]) -> int:
        n = 0
        with self.db.session() as s:
            for b in bars:
                existing = s.execute(select(BarRow).where(
                    BarRow.symbol == b.symbol, BarRow.timeframe == b.timeframe, BarRow.ts == b.ts
                )).scalar_one_or_none()
                if existing is None:
                    s.add(BarRow(symbol=b.symbol, timeframe=b.timeframe, ts=b.ts, open=b.open,
                                 high=b.high, low=b.low, close=b.close, volume=b.volume))
                    n += 1
                else:
                    existing.open, existing.high, existing.low = b.open, b.high, b.low
                    existing.close, existing.volume = b.close, b.volume
        return n

    def bars(self, symbol: str, timeframe: str, limit: int = 400) -> list[Bar]:
        with self.db.session() as s:
            rows = s.execute(select(BarRow).where(BarRow.symbol == symbol,
                                                  BarRow.timeframe == timeframe)
                             .order_by(BarRow.ts.desc()).limit(limit)).scalars().all()
        return [Bar(r.symbol, r.timeframe, r.ts, r.open, r.high, r.low, r.close, r.volume)
                for r in reversed(rows)]

    # --------------------------------------------------------------- orders
    def save_order(self, o: Order, config_version: int) -> None:
        with self.db.session() as s:
            row = s.get(OrderRow, o.id)
            fields = dict(
                idempotency_key=o.idempotency_key, mode=o.mode.value, symbol=o.symbol,
                side=o.side.value, qty=o.qty, order_type=o.order_type.value,
                limit_price=o.limit_price, trigger_price=o.trigger_price, stop=o.stop,
                target=o.target, state=o.state.value, broker_order_id=o.broker_order_id,
                filled_qty=o.filled_qty, avg_fill_price=o.avg_fill_price, purpose=o.purpose,
                decision_id=o.decision_id, strategy=o.strategy, reason=o.reason,
                parent_order_id=o.parent_order_id, created_at=o.created_at,
                updated_at=o.updated_at,
            )
            if row is None:
                s.add(OrderRow(id=o.id, config_version=config_version, **fields))
            else:
                for k, v in fields.items():
                    setattr(row, k, v)

    def add_order_event(self, order_id: str, ts: datetime, frm: OrderState, to: OrderState,
                        detail: dict[str, Any]) -> None:
        with self.db.session() as s:
            s.add(OrderEventRow(order_id=order_id, ts=ts, from_state=frm.value,
                                to_state=to.value, detail=to_jsonable(detail)))

    def idempotency_key_exists(self, key: str) -> bool:
        with self.db.session() as s:
            return s.execute(select(OrderRow.id).where(OrderRow.idempotency_key == key)
                             ).first() is not None

    def orders(self, limit: int = 100, states: Sequence[str] | None = None) -> list[OrderRow]:
        with self.db.session() as s:
            q = select(OrderRow).order_by(OrderRow.created_at.desc()).limit(limit)
            if states:
                q = q.where(OrderRow.state.in_(states))
            return list(s.execute(q).scalars().all())

    def order_events(self, order_id: str) -> list[OrderEventRow]:
        with self.db.session() as s:
            return list(s.execute(select(OrderEventRow).where(OrderEventRow.order_id == order_id)
                                  .order_by(OrderEventRow.id)).scalars().all())

    def count_orders_since(self, since: datetime) -> int:
        with self.db.session() as s:
            q = select(func.count()).select_from(OrderRow).where(
                OrderRow.created_at >= since, OrderRow.purpose != "protective_stop",
                OrderRow.state.notin_([OrderState.REJECTED.value]))
            return int(s.execute(q).scalar_one())

    # ---------------------------------------------------------------- fills
    def save_fill(self, f: Fill) -> None:
        with self.db.session() as s:
            s.add(FillRow(order_id=f.order_id, broker_order_id=f.broker_order_id,
                          symbol=f.symbol, side=f.side.value, ts=f.ts, qty=f.qty, price=f.price,
                          charges=f.charges.as_dict(), charges_total=f.charges.total))

    def fills(self, symbol: str | None = None, limit: int = 1000) -> list[FillRow]:
        with self.db.session() as s:
            q = select(FillRow).order_by(FillRow.ts.asc(), FillRow.id.asc())
            if symbol:
                q = q.where(FillRow.symbol == symbol)
            rows = list(s.execute(q).scalars().all())
        return rows[-limit:]

    # ------------------------------------------------------------ positions
    def replace_positions(self, positions: Iterable[Position]) -> None:
        with self.db.session() as s:
            s.execute(delete(PositionRow))
            for p in positions:
                s.add(PositionRow(symbol=p.symbol, qty=p.qty, avg_price=p.avg_price, stop=p.stop,
                                  target=p.target, initial_stop=p.initial_stop,
                                  highest_close=p.highest_close, opened_at=p.opened_at,
                                  strategy=p.strategy, sector=p.sector))

    def positions(self) -> list[PositionRow]:
        with self.db.session() as s:
            return list(s.execute(select(PositionRow)).scalars().all())

    # ------------------------------------------------- signals and decisions
    def save_signals(self, ts: datetime, bar_ts: datetime, signals: Iterable[Signal],
                     config_version: int) -> None:
        with self.db.session() as s:
            for sig in signals:
                s.add(SignalRow(ts=ts, bar_ts=bar_ts, symbol=sig.symbol, strategy=sig.strategy,
                                action=sig.action, strength=sig.strength,
                                reasons=list(sig.reasons), config_version=config_version))

    def save_decision(self, decision_id: str, ts: datetime, symbol: str, kind: str, score: float,
                      components: dict[str, Any], outcome: str, rejection_reason: str,
                      config_version: int) -> None:
        with self.db.session() as s:
            row = s.get(DecisionRow, decision_id)
            if row is None:
                s.add(DecisionRow(id=decision_id, ts=ts, symbol=symbol, kind=kind, score=score,
                                  components=to_jsonable(components), outcome=outcome,
                                  rejection_reason=rejection_reason,
                                  config_version=config_version))
            else:
                row.outcome, row.rejection_reason = outcome, rejection_reason

    def decisions(self, limit: int = 100, symbol: str | None = None) -> list[DecisionRow]:
        with self.db.session() as s:
            q = select(DecisionRow).order_by(DecisionRow.ts.desc()).limit(limit)
            if symbol:
                q = q.where(DecisionRow.symbol == symbol)
            return list(s.execute(q).scalars().all())

    def signals(self, limit: int = 200) -> list[SignalRow]:
        with self.db.session() as s:
            return list(s.execute(select(SignalRow).order_by(SignalRow.id.desc()).limit(limit))
                        .scalars().all())

    # ----------------------------------------------------------------- risk
    def add_risk_event(self, ts: datetime, check_name: str, result: str,
                       details: dict[str, Any], decision_id: str | None = None,
                       symbol: str | None = None) -> None:
        with self.db.session() as s:
            s.add(RiskEventRow(ts=ts, decision_id=decision_id, symbol=symbol,
                               check_name=check_name, result=result,
                               details=to_jsonable(details)))

    def risk_events(self, limit: int = 200) -> list[RiskEventRow]:
        with self.db.session() as s:
            return list(s.execute(select(RiskEventRow).order_by(RiskEventRow.id.desc())
                                  .limit(limit)).scalars().all())

    # --------------------------------------------------------------- equity
    def add_equity_snapshot(self, ts: datetime, equity: Decimal, cash: Decimal,
                            positions_value: Decimal, realized: Decimal, fees: Decimal,
                            drawdown_pct: float) -> None:
        with self.db.session() as s:
            s.add(EquitySnapshotRow(ts=ts, equity=equity, cash=cash,
                                    positions_value=positions_value, realized=realized,
                                    fees=fees, drawdown_pct=drawdown_pct))

    def equity_curve(self, since: datetime | None = None,
                     limit: int = 5000) -> list[EquitySnapshotRow]:
        with self.db.session() as s:
            q = select(EquitySnapshotRow).order_by(EquitySnapshotRow.ts.desc()).limit(limit)
            if since:
                q = q.where(EquitySnapshotRow.ts >= since)
            return list(reversed(s.execute(q).scalars().all()))

    # ----------------------------------------------------------------- news
    def news_exists(self, news_id: str) -> bool:
        with self.db.session() as s:
            return s.get(NewsItemRow, news_id) is not None

    def add_news(self, row: NewsItemRow) -> bool:
        with self.db.session() as s:
            if s.get(NewsItemRow, row.id) is not None:
                return False
            s.add(row)
            return True

    def unscored_news(self, limit: int = 200) -> list[NewsItemRow]:
        with self.db.session() as s:
            return list(s.execute(select(NewsItemRow).where(NewsItemRow.scored.is_(False))
                                  .order_by(NewsItemRow.published_at.desc()).limit(limit))
                        .scalars().all())

    def mark_scored(self, news_ids: Iterable[str]) -> None:
        ids = list(news_ids)
        if not ids:
            return
        with self.db.session() as s:
            for row in s.execute(select(NewsItemRow).where(NewsItemRow.id.in_(ids))).scalars():
                row.scored = True

    def save_sentiment(self, item: SentimentItem, model: str, prompt_version: str,
                       ts: datetime) -> None:
        with self.db.session() as s:
            exists = s.execute(select(SentimentRow.id).where(
                SentimentRow.news_id == item.news_id, SentimentRow.symbol == item.symbol)).first()
            if exists:
                return
            s.add(SentimentRow(news_id=item.news_id, symbol=item.symbol, score=item.sentiment,
                               confidence=item.confidence, event_type=item.event_type,
                               horizon=item.horizon, is_material=item.is_material,
                               summary=item.summary[:240], model=model,
                               prompt_version=prompt_version, published_at=item.published_at,
                               credibility=item.credibility, created_at=ts))

    def sentiment_items(self, since: datetime, symbol: str | None = None) -> list[SentimentItem]:
        with self.db.session() as s:
            q = select(SentimentRow).where(SentimentRow.published_at >= since)
            if symbol:
                q = q.where(SentimentRow.symbol == symbol)
            rows = s.execute(q.order_by(SentimentRow.published_at.desc())).scalars().all()
        return [SentimentItem(news_id=r.news_id, symbol=r.symbol, sentiment=r.score,
                              confidence=r.confidence, event_type=r.event_type,
                              horizon=r.horizon, is_material=r.is_material, summary=r.summary,
                              published_at=r.published_at, credibility=r.credibility)
                for r in rows]

    def news(self, limit: int = 100, symbol: str | None = None) -> list[dict[str, Any]]:
        with self.db.session() as s:
            q = select(NewsItemRow).order_by(NewsItemRow.published_at.desc()).limit(limit * 3)
            rows = s.execute(q).scalars().all()
            out: list[dict[str, Any]] = []
            for r in rows:
                if symbol and symbol not in (r.symbols or []):
                    continue
                sent = s.execute(select(SentimentRow).where(SentimentRow.news_id == r.id)
                                 ).scalars().all()
                out.append({
                    "id": r.id, "source": r.source, "publisher": r.publisher, "url": r.url,
                    "headline": r.headline, "published_at": r.published_at,
                    "symbols": r.symbols, "credibility": r.credibility, "scored": r.scored,
                    "sentiment": [{"symbol": x.symbol, "score": x.score,
                                   "confidence": x.confidence, "event_type": x.event_type,
                                   "is_material": x.is_material, "summary": x.summary}
                                  for x in sent],
                })
                if len(out) >= limit:
                    break
            return out

    # ------------------------------------------------------------- backtest
    def save_backtest(self, ts: datetime, params: dict[str, Any], metrics: dict[str, Any],
                      equity: list[Any], trades: list[Any]) -> int:
        with self.db.session() as s:
            row = BacktestRunRow(ts=ts, params=to_jsonable(params), metrics=to_jsonable(metrics),
                                 equity_curve=to_jsonable(equity), trades=to_jsonable(trades))
            s.add(row)
            s.flush()
            return row.id

    def backtests(self, limit: int = 20) -> list[BacktestRunRow]:
        with self.db.session() as s:
            return list(s.execute(select(BacktestRunRow).order_by(BacktestRunRow.id.desc())
                                  .limit(limit)).scalars().all())

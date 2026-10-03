"""Validated config schemas. Bounds reject obviously unsafe values."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

CONFIG_DIR = Path(__file__).parent


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BreakerConfig(_Strict):
    daily_loss_auto_reset: bool = True
    broker_error_threshold: int = Field(3, ge=1, le=20)
    reject_storm_threshold: int = Field(5, ge=1, le=50)
    reject_storm_window_min: int = Field(10, ge=1, le=120)
    stale_data_halt_sec: int = Field(300, ge=30, le=3600)


class KillSwitchConfig(_Strict):
    default_action: Literal["block", "flatten"] = "block"
    watchdog_heartbeat_timeout_sec: int = Field(90, ge=15, le=900)
    watchdog_triggers_kill_switch: bool = True


class RiskConfig(_Strict):
    risk_per_trade_pct: float = Field(1.0, gt=0, le=2.0)
    max_position_pct: float = Field(20.0, gt=0, le=50.0)
    max_sector_pct: float = Field(35.0, gt=0, le=100.0)
    max_open_positions: int = Field(5, ge=1, le=30)
    max_daily_loss_pct: float = Field(2.0, gt=0, le=5.0)
    max_weekly_loss_pct: float = Field(4.0, gt=0, le=10.0)
    max_drawdown_pct: float = Field(10.0, gt=0, le=25.0)
    max_orders_per_minute: int = Field(5, ge=1, le=30)
    max_orders_per_day: int = Field(20, ge=1, le=200)
    max_order_value: Decimal = Field(Decimal(25000), gt=0)
    max_price_deviation_pct: float = Field(2.0, gt=0, le=10.0)
    max_circuit_band_pct: float = Field(20.0, gt=0, le=20.0)
    max_adv_participation_pct: float = Field(1.0, gt=0, le=10.0)
    min_cash_buffer_pct: float = Field(10.0, ge=0, le=90.0)
    max_data_staleness_sec: int = Field(120, ge=5, le=3600)
    no_entry_first_minutes: int = Field(5, ge=0, le=120)
    no_entry_last_minutes: int = Field(15, ge=0, le=120)
    min_stop_distance_pct: float = Field(0.5, gt=0, le=5.0)
    max_stop_distance_pct: float = Field(15.0, gt=0, le=30.0)
    min_edge_to_cost_ratio: float = Field(3.0, ge=1.0, le=50.0)
    max_correlation: float = Field(0.85, gt=0, le=1.0)
    allow_averaging: Literal[False] = False
    breakers: BreakerConfig = BreakerConfig()
    kill_switch: KillSwitchConfig = KillSwitchConfig()

    @model_validator(mode="after")
    def _consistent(self) -> RiskConfig:
        if self.max_weekly_loss_pct < self.max_daily_loss_pct:
            raise ValueError("max_weekly_loss_pct must be >= max_daily_loss_pct")
        if self.min_stop_distance_pct >= self.max_stop_distance_pct:
            raise ValueError("min_stop_distance_pct must be < max_stop_distance_pct")
        return self


class UniverseConfig(_Strict):
    symbols: list[str] = []
    min_price: float = Field(20, ge=0)
    min_avg_traded_value: float = Field(50_000_000, ge=0)
    min_history_bars: int = Field(60, ge=20)
    ban_list: list[str] = []


class TrendParams(_Strict):
    enabled: bool = True
    fast_ema: int = Field(20, ge=2, le=200)
    slow_ema: int = Field(50, ge=3, le=400)
    rsi_period: int = Field(14, ge=2, le=50)
    rsi_min: float = Field(50, ge=0, le=100)
    rsi_max: float = Field(70, ge=0, le=100)
    volume_period: int = Field(20, ge=2, le=200)
    atr_period: int = Field(14, ge=2, le=100)
    atr_stop_mult: float = Field(2.0, gt=0, le=10)
    reward_risk: float = Field(2.0, ge=0.5, le=10)
    max_holding_days: int = Field(30, ge=1, le=365)

    @model_validator(mode="after")
    def _order(self) -> TrendParams:
        if self.fast_ema >= self.slow_ema:
            raise ValueError("fast_ema must be < slow_ema")
        if self.rsi_min >= self.rsi_max:
            raise ValueError("rsi_min must be < rsi_max")
        return self


class BreakoutParams(_Strict):
    enabled: bool = True
    lookback: int = Field(20, ge=5, le=250)
    volume_mult: float = Field(1.5, ge=1.0, le=10)
    atr_period: int = Field(14, ge=2, le=100)
    atr_stop_mult: float = Field(1.5, gt=0, le=10)
    reward_risk: float = Field(2.0, ge=0.5, le=10)
    squeeze_lookback: int = Field(20, ge=5, le=250)
    max_holding_days: int = Field(20, ge=1, le=365)


class StrategiesConfig(_Strict):
    trend: TrendParams = TrendParams()
    breakout: BreakoutParams = BreakoutParams()


class WeightsConfig(_Strict):
    trend: float = Field(0.35, ge=0)
    momentum: float = Field(0.25, ge=0)
    volume: float = Field(0.15, ge=0)
    sentiment: float = Field(0.25, ge=0)

    @model_validator(mode="after")
    def _positive(self) -> WeightsConfig:
        if self.trend + self.momentum + self.volume + self.sentiment <= 0:
            raise ValueError("weights must not all be zero")
        return self


class DecisionConfig(_Strict):
    weights: WeightsConfig = WeightsConfig()
    entry_threshold: float = Field(0.60, ge=0, le=1)
    exit_threshold: float = Field(0.30, ge=0, le=1)
    trailing_after_r: float = Field(1.0, ge=0, le=10)
    trailing_atr_mult: float = Field(2.0, gt=0, le=10)
    intent_ttl_bars: int = Field(1, ge=1, le=5)

    @model_validator(mode="after")
    def _thresholds(self) -> DecisionConfig:
        if self.exit_threshold >= self.entry_threshold:
            raise ValueError("exit_threshold must be < entry_threshold")
        return self


class RotationConfig(_Strict):
    enabled: bool = True
    min_score_gap: float = Field(0.15, ge=0.05, le=1)
    min_hold_days: int = Field(3, ge=1, le=60)


class RegimeConfig(_Strict):
    enabled: bool = True
    index_symbol: str = "NIFTY50"
    index_ema: int = Field(200, ge=20, le=400)
    vix_symbol: str = "INDIAVIX"
    vix_reduce_above: float = Field(22, gt=0)
    event_blackout_dates: list[date] = []
    symbol_blackouts: dict[str, list[date]] = {}


class SentimentConfig(_Strict):
    enabled: bool = True
    lookback_days: int = Field(30, ge=1, le=90)
    half_life_days: float = Field(3.0, gt=0, le=60)
    min_confidence: float = Field(0.5, ge=0, le=1)
    veto_score: float = Field(-0.5, ge=-1, le=0)
    veto_window_days: int = Field(7, ge=1, le=30)
    exit_on_negative_material: bool = True
    novelty_move_pct: float = Field(5.0, gt=0, le=50)
    max_items_per_call: int = Field(12, ge=1, le=30)


class PaperBrokerConfig(_Strict):
    spread_bps: float = Field(5, ge=0, le=200)
    base_slippage_bps: float = Field(5, ge=0, le=200)
    impact_bps_per_pct_adv: float = Field(10, ge=0, le=500)
    max_fill_fraction_of_bar_volume: float = Field(0.1, gt=0, le=1)
    random_reject_prob: float = Field(0.0, ge=0, le=0.5)
    seed: int = 7


class CostConfig(_Strict):
    brokerage_pct: Decimal = Field(Decimal(0), ge=0)
    brokerage_max: Decimal = Field(Decimal(20), ge=0)
    stt_pct: Decimal = Field(Decimal("0.1"), ge=0)
    exchange_txn_pct: Decimal = Field(Decimal("0.00297"), ge=0)
    sebi_per_crore: Decimal = Field(Decimal(10), ge=0)
    stamp_duty_buy_pct: Decimal = Field(Decimal("0.015"), ge=0)
    gst_pct: Decimal = Field(Decimal(18), ge=0)
    dp_charge_per_sell: Decimal = Field(Decimal("15.93"), ge=0)


class StrategyConfig(_Strict):
    capital: Decimal = Field(Decimal(5000), gt=0)
    timeframe: Literal["1d", "15m"] = "1d"
    product: Literal["CNC"] = "CNC"
    universe: UniverseConfig = UniverseConfig()
    strategies: StrategiesConfig = StrategiesConfig()
    decision: DecisionConfig = DecisionConfig()
    rotation: RotationConfig = RotationConfig()
    regime: RegimeConfig = RegimeConfig()
    sentiment: SentimentConfig = SentimentConfig()
    paper_broker: PaperBrokerConfig = PaperBrokerConfig()
    costs: CostConfig = CostConfig()


class AppConfig(_Strict):
    risk: RiskConfig
    strategy: StrategyConfig

    def fingerprint(self) -> str:
        blob = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:12]


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open() as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping")
    return data


def default_config() -> AppConfig:
    return AppConfig(
        risk=RiskConfig.model_validate(load_yaml(CONFIG_DIR / "risk_config.yaml")),
        strategy=StrategyConfig.model_validate(load_yaml(CONFIG_DIR / "strategy_config.yaml")),
    )

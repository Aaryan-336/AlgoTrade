"""Wires settings, database, providers and the engine together."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from algotrade.audit.logger import AuditLog
from algotrade.brokers.costs import CostModel
from algotrade.brokers.paper import PaperBroker
from algotrade.config.models import AppConfig, default_config
from algotrade.config.settings import Settings
from algotrade.core.clock import Clock, MarketCalendar, SystemClock
from algotrade.data.providers.base import MarketDataProvider
from algotrade.data.providers.replay import ReplayProvider
from algotrade.data.providers.upstox import UpstoxAuth, UpstoxProvider
from algotrade.data.providers.yfinance_provider import YFinanceProvider
from algotrade.data.universe import Universe
from algotrade.db.migrate import migrate
from algotrade.db.repository import Repository
from algotrade.db.session import Database
from algotrade.engine.alerts import Notifier
from algotrade.engine.core import TradingEngine
from algotrade.news.sentiment import GroqSentimentClient
from algotrade.news.service import NewsService


class Runtime:
    def __init__(self, settings: Settings, db: Database | None = None,
                 clock: Clock | None = None, provider: MarketDataProvider | None = None,
                 holidays: list[Any] | None = None) -> None:
        self.settings = settings
        self.db = db or Database(settings.database_url)
        migrate(self.db)
        self.repo = Repository(self.db)
        self.audit = AuditLog(self.db)
        self.clock = clock or SystemClock()
        self.calendar = MarketCalendar(holidays or [])
        self.http = httpx.AsyncClient(timeout=20)
        self.notifier = Notifier(
            settings.telegram_bot_token.get_secret_value() if settings.telegram_bot_token
            else None, settings.telegram_chat_id)
        self.upstox_auth = UpstoxAuth(
            settings.upstox_api_key.get_secret_value() if settings.upstox_api_key else None,
            settings.upstox_api_secret.get_secret_value() if settings.upstox_api_secret else None,
            settings.upstox_redirect_uri)
        self.config_version, self.config = self._load_or_seed_config()
        self._seed_profiles()
        self.universe = Universe.from_config(self.config.strategy.universe)
        self.provider: MarketDataProvider = provider or self._make_provider()
        # While Upstox is not logged in (or the account is not active yet), daily
        # history for charts and backtests comes from delayed Yahoo data. Trading
        # stays blocked: there are no fresh live prices, so risk checks fail closed.
        self.fallback: MarketDataProvider | None = (
            YFinanceProvider({k: v["yfinance"] for k, v in self.universe.indices.items()})
            if provider is None and settings.data_provider == "upstox" else None)
        self.llm = GroqSentimentClient(
            settings.groq_api_key.get_secret_value() if settings.groq_api_key else None,
            settings.groq_model, settings.groq_min_interval_sec)
        self.news = NewsService(self.repo, self.llm, self.audit, self.http)
        self.engine = self.build_engine()

    # ---------------------------------------------------------------- setup
    def _load_or_seed_config(self) -> tuple[int, AppConfig]:
        latest = self.repo.latest_config()
        if latest:
            return latest
        cfg = default_config()
        v = self.repo.save_config(cfg, self.clock.now(), "system", "initial defaults")
        return v, cfg

    def _seed_profiles(self) -> None:
        """First run: the current config becomes the 'Default' version, marked live."""
        if self.repo.profiles():
            return
        now = self.clock.now()
        pid = self.repo.save_profile("Default", "Starting configuration", self.config, now)
        self._mark_live(pid, "Default", now)

    def _mark_live(self, profile_id: int, name: str, now: datetime) -> None:
        self.repo.set_state("live_profile", {"id": profile_id, "name": name,
                                             "fingerprint": self.config.fingerprint()}, now)

    # ------------------------------------------------------------ versions
    def live_profile_id(self) -> int | None:
        st = self.repo.get_state("live_profile") or {}
        return int(st["id"]) if st.get("id") else None

    @staticmethod
    def profile_config(row: Any) -> AppConfig:
        from algotrade.config.models import RiskConfig, StrategyConfig

        return AppConfig(risk=RiskConfig.model_validate(row.risk),
                         strategy=StrategyConfig.model_validate(row.strategy))

    def save_profile(self, name: str, description: str, cfg: AppConfig, now: datetime,
                     profile_id: int | None = None) -> int:
        """Create or update a strategy version. The live version is frozen during
        market hours; editing it after the close makes the edit live too."""
        name = name.strip()
        if not name:
            raise ValueError("version name is required")
        clash = self.repo.profile_by_name(name)
        if clash is not None and clash.id != profile_id:
            raise ValueError(f"a version named '{name}' already exists")
        is_live = profile_id is not None and profile_id == self.live_profile_id()
        if is_live and self.calendar.is_open(now):
            raise PermissionError("the live version can't be changed during market hours")
        if profile_id is not None and self.repo.profile(profile_id) is None:
            raise LookupError("version not found")
        pid = self.repo.save_profile(name, description, cfg, now, profile_id)
        self.audit.record(now, "owner", "profile.saved", {"id": pid, "name": name,
                                                          "fingerprint": cfg.fingerprint()})
        if is_live:
            self.activate_profile(pid, now, reason=f"edited live version '{name}'")
        return pid

    def delete_profile(self, profile_id: int, now: datetime) -> None:
        if profile_id == self.live_profile_id():
            raise PermissionError("make another version live before deleting this one")
        if not self.repo.delete_profile(profile_id):
            raise LookupError("version not found")
        self.audit.record(now, "owner", "profile.deleted", {"id": profile_id})

    def activate_profile(self, profile_id: int, now: datetime, reason: str = "") -> int:
        """Make a version the live trading config (outside market hours only)."""
        row = self.repo.profile(profile_id)
        if row is None:
            raise LookupError("version not found")
        cfg = self.profile_config(row)
        version = self.save_config(cfg, reason or f"activated version '{row.name}'", now)
        self.repo.set_state("live_profile", {"id": row.id, "name": row.name,
                                             "fingerprint": cfg.fingerprint(),
                                             "config_version": version}, now)
        self.reload_config()
        return version

    def _make_provider(self) -> MarketDataProvider:
        idx = self.universe.indices
        if self.settings.data_provider == "upstox":
            return UpstoxProvider(self.upstox_auth, {k: v["upstox"] for k, v in idx.items()},
                                  client=self.http)
        if self.settings.data_provider == "yfinance":
            return YFinanceProvider({k: v["yfinance"] for k, v in idx.items()})
        return ReplayProvider({}, self.clock)

    def history_provider(self) -> MarketDataProvider | None:
        if self.provider.is_ready():
            return self.provider
        return self.fallback

    def build_engine(self) -> TradingEngine:
        sc = self.config.strategy
        state = self.repo.get_state("paper_ledger")
        now = self.clock.now()

        def persist(snapshot: dict[str, Any]) -> None:
            self.repo.set_state("paper_ledger", snapshot, self.clock.now())

        broker = PaperBroker(sc.paper_broker, CostModel(sc.costs), sc.capital, persist, state)
        engine = TradingEngine(self.config, self.config_version, self.universe, self.calendar,
                               broker, self.repo, self.audit, self.notifier)
        engine.restore(now)
        return engine

    def reload_config(self) -> bool:
        """Adopt the newest saved config. Only called while the market is closed."""
        latest = self.repo.latest_config()
        if not latest or latest[0] == self.config_version:
            return False
        self.config_version, self.config = latest
        self.universe = Universe.from_config(self.config.strategy.universe)
        old = self.engine
        self.engine = self.build_engine()
        self.engine.daily, self.engine.prev_close = old.daily, old.prev_close
        self.engine.prices, self.engine.price_ts = old.prices, old.price_ts
        self.audit.record(self.clock.now(), "system", "config.activated",
                          {"version": self.config_version,
                           "fingerprint": self.config.fingerprint()},
                          config_version=self.config_version)
        return True

    def save_config(self, cfg: AppConfig, reason: str, now: datetime) -> int:
        if self.calendar.is_open(now):
            # Rule I-12: limits never change during market hours.
            raise PermissionError("config changes are not allowed during market hours")
        v = self.repo.save_config(cfg, now, "owner", reason)
        self.audit.record(now, "owner", "config.saved", {"version": v, "reason": reason,
                                                         "fingerprint": cfg.fingerprint()},
                          config_version=v)
        return v

    async def aclose(self) -> None:
        await self.http.aclose()

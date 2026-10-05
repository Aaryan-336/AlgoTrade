"""Free, delayed Yahoo Finance data. Research and backtests only: it is
unofficial, delayed and sometimes wrong, so it must never drive live
decisions (docs/data-sources.md §2)."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from algotrade.core.types import Bar, ProviderHealth, Tick
from algotrade.data.providers.base import ProviderError

_INTERVAL = {"1d": "1d", "15m": "15m"}


def yahoo_ticker(symbol: str, index_map: dict[str, str]) -> str:
    return index_map.get(symbol, f"{symbol}.NS")


class YFinanceProvider:
    name = "yfinance"

    def __init__(self, index_map: dict[str, str]) -> None:
        self.index_map = index_map
        self._last_ok: datetime | None = None
        self._err = ""

    def _frame_to_bars(self, symbol: str, timeframe: str, df: Any) -> list[Bar]:
        bars: list[Bar] = []
        for ts, row in df.iterrows():
            o, h, lo, c = (row["Open"], row["High"], row["Low"], row["Close"])
            if any(v != v for v in (o, h, lo, c)):  # NaN rows
                continue
            when = ts.to_pydatetime()
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            bars.append(Bar(symbol, timeframe, when.astimezone(UTC),
                            Decimal(str(round(float(o), 2))), Decimal(str(round(float(h), 2))),
                            Decimal(str(round(float(lo), 2))), Decimal(str(round(float(c), 2))),
                            int(row.get("Volume", 0) or 0)))
        return bars

    def _download(self, symbol: str, timeframe: str, start: date, end: date) -> list[Bar]:
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - optional extra
            raise ProviderError("install the 'research' extra to use yfinance") from exc
        try:
            df = yf.Ticker(yahoo_ticker(symbol, self.index_map)).history(
                start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                interval=_INTERVAL[timeframe], auto_adjust=True)
        except Exception as exc:
            self._err = f"{type(exc).__name__}"
            raise ProviderError(f"yfinance failed for {symbol}: {exc}") from exc
        self._last_ok = datetime.now(UTC)
        self._err = ""
        return self._frame_to_bars(symbol, timeframe, df)

    async def history(self, symbol: str, timeframe: str, start: date, end: date) -> list[Bar]:
        return await asyncio.to_thread(self._download, symbol, timeframe, start, end)

    async def intraday(self, symbol: str, timeframe: str) -> list[Bar]:
        today = datetime.now(UTC).date()
        bars = await self.history(symbol, timeframe, today, today)
        now = datetime.now(UTC)
        step = timedelta(minutes=15) if timeframe == "15m" else timedelta(hours=15, minutes=30)
        return [b for b in bars if b.ts + step <= now]

    async def ltp(self, symbols: list[str]) -> list[Tick]:
        now = datetime.now(UTC)
        out: list[Tick] = []
        for s in symbols:
            bars = await asyncio.to_thread(self._download, s, "15m", now.date() - timedelta(days=3),
                                           now.date())
            if bars:
                # Stamp with the bar's real end time so staleness checks see the delay.
                last = bars[-1]
                out.append(Tick(s, min(now, last.ts + timedelta(minutes=15)), last.close, None))
        return out

    def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, not self._err, self._last_ok,
                              self._err or "delayed data (research only)")

    def is_ready(self) -> bool:
        return True

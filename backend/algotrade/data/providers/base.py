"""Market data provider interface (docs/design.md §2.1).

Decisions run on bars, so live providers are polled rather than streamed:
``ltp`` feeds fills, stops and staleness checks; ``history``/``intraday``
feed indicators. Adding a provider must not change any other module.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from algotrade.core.types import Bar, ProviderHealth, Tick


class ProviderError(RuntimeError):
    """Raised on any provider failure. Callers treat it as missing data."""


class MarketDataProvider(Protocol):
    name: str

    async def ltp(self, symbols: list[str]) -> list[Tick]: ...

    async def history(self, symbol: str, timeframe: str, start: date, end: date) -> list[Bar]:
        """Completed bars, oldest first. ``timeframe`` is "1d" or "15m"."""
        ...

    async def intraday(self, symbol: str, timeframe: str) -> list[Bar]:
        """Today's completed bars, oldest first."""
        ...

    def health(self) -> ProviderHealth: ...

    def is_ready(self) -> bool:
        """False when the provider cannot serve data (e.g. not logged in)."""
        ...

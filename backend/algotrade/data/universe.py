"""Tradable universe and instrument metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from algotrade.config.models import UniverseConfig
from algotrade.core.types import Instrument

_DATA = Path(__file__).parent / "nifty50.json"


def _load() -> dict[str, Any]:
    with _DATA.open() as fh:
        data: dict[str, Any] = json.load(fh)
    return data


class Universe:
    def __init__(self, instruments: list[Instrument], indices: dict[str, dict[str, str]]) -> None:
        self._by_symbol = {i.symbol: i for i in instruments}
        self.indices = indices

    @classmethod
    def from_config(cls, cfg: UniverseConfig) -> Universe:
        raw = _load()
        all_instruments = [
            Instrument(symbol=r["symbol"], name=r["name"], sector=r["sector"],
                       aliases=tuple(r.get("aliases", ())))
            for r in raw["instruments"]
        ]
        wanted = {s.upper() for s in cfg.symbols}
        banned = {s.upper() for s in cfg.ban_list}
        known = {i.symbol for i in all_instruments}
        chosen = [i for i in all_instruments if (not wanted or i.symbol in wanted)]
        # Watchlist symbols outside the bundled list are allowed with an "Unknown"
        # sector; the sector cap then treats them as one bucket (conservative).
        for sym in sorted(wanted - known):
            chosen.append(Instrument(symbol=sym, name=sym, sector="Unknown", aliases=(sym,)))
        chosen = [i for i in chosen if i.symbol not in banned]
        return cls(chosen, raw.get("indices", {}))

    @property
    def symbols(self) -> list[str]:
        return list(self._by_symbol)

    def get(self, symbol: str) -> Instrument | None:
        return self._by_symbol.get(symbol)

    def __contains__(self, symbol: object) -> bool:
        return symbol in self._by_symbol

    def instruments(self) -> list[Instrument]:
        return list(self._by_symbol.values())

    def sector(self, symbol: str) -> str:
        inst = self._by_symbol.get(symbol)
        return inst.sector if inst else "Unknown"

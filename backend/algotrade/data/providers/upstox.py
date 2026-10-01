"""Upstox market data adapter (official REST API, polled).

Auth is Upstox's OAuth2 code flow: the owner logs in once per day from the
dashboard; the access token lives in memory only and is never written to
disk, logs or the database (docs/security.md §2). The account password and
TOTP are never handled by this code.

Endpoints used (verify against https://upstox.com/developer/api-documentation):
  GET  /v2/login/authorization/dialog            (browser redirect)
  POST /v2/login/authorization/token             (code -> access token)
  GET  /v3/market-quote/ltp?instrument_key=...
  GET  /v3/historical-candle/{key}/{unit}/{interval}/{to}/{from}
  GET  /v3/historical-candle/intraday/{key}/{unit}/{interval}
Instrument master: https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz
"""

from __future__ import annotations

import asyncio
import gzip
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from algotrade.core.types import Bar, ProviderHealth, Tick
from algotrade.data.providers.base import ProviderError

BASE = "https://api.upstox.com"
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
_TF = {"1d": ("days", "1"), "15m": ("minutes", "15")}
# A bar is complete this long after its timestamp. Daily candles are stamped
# 00:00 IST, so they complete at the 15:30 IST close.
_TF_LEN = {"1d": timedelta(hours=15, minutes=30), "15m": timedelta(minutes=15)}


def _upstox_error(r: httpx.Response) -> str:
    """Upstox's own error code and message (never contains our secrets)."""
    try:
        errs = r.json().get("errors") or []
    except ValueError:
        return ""
    parts = [f"{e.get('errorCode', '')} {e.get('message', '')}".strip()
             for e in errs if isinstance(e, dict)]
    return f" ({'; '.join(p for p in parts if p)[:200]})" if parts else ""


class UpstoxAuth:
    def __init__(self, api_key: str | None, api_secret: str | None, redirect_uri: str) -> None:
        # Stray spaces or quotes from copy-paste are a common cause of 401s.
        self.api_key = api_key.strip().strip("'\"") if api_key else None
        self.api_secret = api_secret.strip().strip("'\"") if api_secret else None
        self.redirect_uri = redirect_uri.strip()
        self._token: str | None = None
        self.token_set_at: datetime | None = None
        self.user: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    @property
    def token(self) -> str | None:
        return self._token

    def login_url(self, state: str) -> str:
        if not self.api_key:
            raise ProviderError("UPSTOX_API_KEY is not set")
        q = urlencode({"response_type": "code", "client_id": self.api_key,
                       "redirect_uri": self.redirect_uri, "state": state})
        return f"{BASE}/v2/login/authorization/dialog?{q}"

    async def exchange_code(self, code: str, client: httpx.AsyncClient | None = None) -> None:
        if not self.configured:
            raise ProviderError("Upstox API key/secret not configured")
        data = {"code": code, "client_id": self.api_key, "client_secret": self.api_secret,
                "redirect_uri": self.redirect_uri, "grant_type": "authorization_code"}
        own = client is None
        c = client or httpx.AsyncClient(timeout=15)
        try:
            r = await c.post(f"{BASE}/v2/login/authorization/token", data=data,
                             headers={"accept": "application/json"})
        finally:
            if own:
                await c.aclose()
        if r.status_code != 200:
            raise ProviderError(f"token exchange failed: HTTP {r.status_code}"
                                f"{_upstox_error(r)}. Check UPSTOX_API_SECRET and that "
                                f"UPSTOX_REDIRECT_URI ({self.redirect_uri}) matches the app "
                                "exactly; each login code works only once.")
        body = r.json()
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise ProviderError("token exchange returned no access_token")
        self._token = token
        self.token_set_at = datetime.now(UTC)
        self.user = str(body.get("user_name") or body.get("user_id") or "")

    def set_token(self, token: str) -> None:
        self._token = token
        self.token_set_at = datetime.now(UTC)

    def clear(self) -> None:
        self._token = None
        self.token_set_at = None


def _parse_candles(symbol: str, timeframe: str, payload: dict[str, Any]) -> list[Bar]:
    if payload.get("status") != "success":
        raise ProviderError(f"candle request failed for {symbol}")
    rows = (payload.get("data") or {}).get("candles") or []
    bars: list[Bar] = []
    for row in rows:
        ts = datetime.fromisoformat(row[0]).astimezone(UTC)
        bars.append(Bar(symbol, timeframe, ts, Decimal(str(row[1])), Decimal(str(row[2])),
                        Decimal(str(row[3])), Decimal(str(row[4])), int(row[5])))
    bars.sort(key=lambda b: b.ts)  # Upstox returns newest first
    return bars


class UpstoxProvider:
    name = "upstox"

    def __init__(self, auth: UpstoxAuth, index_keys: dict[str, str],
                 client: httpx.AsyncClient | None = None,
                 instrument_keys: dict[str, str] | None = None) -> None:
        self.auth = auth
        self._client = client or httpx.AsyncClient(timeout=15)
        self._keys: dict[str, str] = dict(instrument_keys or {})
        self._keys.update(index_keys)
        self._rev: dict[str, str] = {v: k for k, v in self._keys.items()}
        self._sem = asyncio.Semaphore(5)
        self._last_ok: datetime | None = None
        self._last_error = ""

    # ------------------------------------------------------------ helpers
    def _headers(self) -> dict[str, str]:
        if not self.auth.token:
            raise ProviderError("Upstox not logged in")
        return {"Authorization": f"Bearer {self.auth.token}", "Accept": "application/json"}

    async def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        async with self._sem:
            try:
                r = await self._client.get(f"{BASE}{path}", params=params,
                                           headers=self._headers())
            except httpx.HTTPError as exc:
                self._last_error = f"network: {type(exc).__name__}"
                raise ProviderError(self._last_error) from exc
        if r.status_code == 401:
            self.auth.clear()
            self._last_error = "token expired or invalid; log in again"
            raise ProviderError(self._last_error)
        if r.status_code != 200:
            self._last_error = f"HTTP {r.status_code} on {path.split('/')[2]}"
            raise ProviderError(self._last_error)
        self._last_ok = datetime.now(UTC)
        self._last_error = ""
        data: dict[str, Any] = r.json()
        return data

    async def load_instruments(self, symbols: list[str]) -> None:
        """Map trading symbols to Upstox instrument keys from the public master."""
        missing = [s for s in symbols if s not in self._keys]
        if not missing:
            return
        try:
            r = await self._client.get(INSTRUMENTS_URL)
            r.raise_for_status()
            rows = json.loads(gzip.decompress(r.content))
        except (httpx.HTTPError, OSError, ValueError) as exc:
            raise ProviderError(f"instrument master download failed: {exc}") from exc
        self.apply_instrument_master(rows)

    def apply_instrument_master(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            if row.get("segment") == "NSE_EQ" and row.get("instrument_type") == "EQ":
                sym, key = row.get("trading_symbol"), row.get("instrument_key")
                if isinstance(sym, str) and isinstance(key, str):
                    self._keys.setdefault(sym, key)
        self._rev = {v: k for k, v in self._keys.items()}

    def key_for(self, symbol: str) -> str:
        try:
            return self._keys[symbol]
        except KeyError as exc:
            raise ProviderError(f"no Upstox instrument key for {symbol}") from exc

    # ----------------------------------------------------------- interface
    async def ltp(self, symbols: list[str]) -> list[Tick]:
        keys = [self._keys[s] for s in symbols if s in self._keys]
        now = datetime.now(UTC)
        ticks: list[Tick] = []
        for i in range(0, len(keys), 450):
            chunk = keys[i:i + 450]
            body = await self._get("/v3/market-quote/ltp",
                                   {"instrument_key": ",".join(chunk)})
            for entry in (body.get("data") or {}).values():
                token = entry.get("instrument_token")
                sym = self._rev.get(str(token).replace(":", "|")) if token else None
                price = entry.get("last_price")
                if sym is None or price is None:
                    continue
                vol = entry.get("volume")
                ticks.append(Tick(sym, now, Decimal(str(price)),
                                  int(vol) if vol is not None else None))
        return ticks

    async def history(self, symbol: str, timeframe: str, start: date, end: date) -> list[Bar]:
        unit, interval = _TF[timeframe]
        key = quote(self.key_for(symbol), safe="")
        body = await self._get(f"/v3/historical-candle/{key}/{unit}/{interval}/"
                               f"{end.isoformat()}/{start.isoformat()}")
        return _parse_candles(symbol, timeframe, body)

    async def intraday(self, symbol: str, timeframe: str) -> list[Bar]:
        unit, interval = _TF[timeframe]
        key = quote(self.key_for(symbol), safe="")
        body = await self._get(f"/v3/historical-candle/intraday/{key}/{unit}/{interval}")
        now = datetime.now(UTC)
        # Drop the still-forming candle: only completed bars feed decisions.
        return [b for b in _parse_candles(symbol, timeframe, body)
                if b.ts + _TF_LEN[timeframe] <= now]

    def health(self) -> ProviderHealth:
        connected = self.auth.token is not None and not self._last_error
        msg = self._last_error or ("ok" if self.auth.token else "not logged in")
        return ProviderHealth(self.name, connected, self._last_ok, msg)

    def is_ready(self) -> bool:
        return self.auth.token is not None

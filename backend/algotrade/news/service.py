"""News pipeline: fetch -> dedupe -> store -> score with the LLM -> store."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

import httpx

from algotrade.audit.logger import AuditLog
from algotrade.config.models import SentimentConfig
from algotrade.core.types import Instrument
from algotrade.db.models import NewsItemRow
from algotrade.db.repository import Repository
from algotrade.news.sentiment import PROMPT_VERSION, GroqSentimentClient, Headline
from algotrade.news.sources import fetch_all


class NewsService:
    def __init__(self, repo: Repository, llm: GroqSentimentClient, audit: AuditLog | None = None,
                 client: httpx.AsyncClient | None = None) -> None:
        self.repo = repo
        self.llm = llm
        self.audit = audit
        self._client = client or httpx.AsyncClient()
        self.last_run: datetime | None = None
        self.last_errors: list[str] = []
        self.last_counts: dict[str, int] = {}

    async def ingest(self, instruments: list[Instrument], now: datetime, days: int) -> int:
        items, errors = await fetch_all(instruments, now, days, self._client)
        added = 0
        for n in items:
            if self.repo.add_news(NewsItemRow(
                    id=n.id, source=n.source, publisher=n.publisher, url=n.url,
                    headline=n.headline, snippet=n.snippet, published_at=n.published_at,
                    fetched_at=now, symbols=list(n.symbols), credibility=n.credibility,
                    scored=False)):
                added += 1
        self.last_errors = errors
        return added

    async def score_pending(self, instruments: list[Instrument], now: datetime,
                            cfg: SentimentConfig, limit: int = 300) -> int:
        if not self.llm.configured:
            return 0
        names = {i.symbol: i.name for i in instruments}
        pending = self.repo.unscored_news(limit)
        by_symbol: dict[str, list[Headline]] = defaultdict(list)
        for row in pending:
            for sym in row.symbols or []:
                if sym in names:
                    by_symbol[sym].append(Headline(row.id, row.headline, row.snippet,
                                                   row.publisher, row.published_at,
                                                   row.credibility))
        scored = 0
        done_ids: set[str] = set()
        failed_ids: set[str] = set()
        for sym, heads in by_symbol.items():
            for i in range(0, len(heads), cfg.max_items_per_call):
                batch = heads[i:i + cfg.max_items_per_call]
                results = await self.llm.score(sym, names[sym], batch)
                if not results and self.llm.last_error:
                    failed_ids.update(h.id for h in batch)
                    continue
                for item in results:
                    self.repo.save_sentiment(item, self.llm.model, PROMPT_VERSION, now)
                    scored += 1
                # Items the model skipped or answered invalidly stay neutral.
                done_ids.update(h.id for h in batch)
        self.repo.mark_scored(done_ids - failed_ids)
        return scored

    async def run(self, instruments: list[Instrument], now: datetime,
                  cfg: SentimentConfig) -> dict[str, int]:
        added = await self.ingest(instruments, now, cfg.lookback_days)
        scored = await self.score_pending(instruments, now, cfg)
        self.last_run = now
        self.last_counts = {"added": added, "scored": scored}
        if self.audit:
            self.audit.record(now, "news", "news.cycle",
                              {"added": added, "scored": scored, "errors": self.last_errors[:10],
                               "llm_error": self.llm.last_error})
        return self.last_counts

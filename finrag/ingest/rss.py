"""
finrag.ingest.rss — generic RSS/Atom adapter.

One class, configured per feed. Each feed maps to a source_name that must exist
in the `sources` table (so authority_rank/dedup work). feedparser handles both
RSS and Atom and the messy date formats Indian financial sites emit.

Body handling: RSS gives title + summary cheaply. We store the summary as the
initial body. Full-body hydration (fetching the article page) is deferred to a
later, entity-gated step so we only pull full text for items about held names —
polite and cheap. The hook is `hydrate=False` here.
"""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from typing import Iterable

import feedparser

from finrag.ingest.base import RawItem, normalize_text
from finrag.ingest.http import HttpClient


@dataclass(frozen=True)
class FeedSpec:
    source_name: str          # must match SOURCE_CATALOG / sources.name
    url: str
    min_interval: float = 1.0


def _parsed_time(entry) -> dt.datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return dt.datetime.fromtimestamp(time.mktime(t), tz=dt.timezone.utc)
    return None


def _clean_summary(entry) -> str:
    raw = entry.get("summary", "") or ""
    # strip any HTML tags feedparser left in the summary
    try:
        from bs4 import BeautifulSoup
        raw = BeautifulSoup(raw, "lxml").get_text(" ")
    except Exception:
        pass
    return normalize_text(raw)


class RssAdapter:
    """Adapter for a single RSS/Atom feed."""

    def __init__(self, spec: FeedSpec) -> None:
        self.spec = spec
        self.name = spec.source_name

    def fetch(self, client: HttpClient | None = None) -> Iterable[RawItem]:
        own = client is None
        client = client or HttpClient(min_interval=self.spec.min_interval)
        try:
            resp = client.get(self.spec.url)
            feed = feedparser.parse(resp.content)
            for e in feed.entries:
                title = normalize_text(e.get("title"))
                if not title:
                    continue
                yield RawItem(
                    source_name=self.spec.source_name,
                    title=title,
                    url=e.get("link"),
                    body=_clean_summary(e),
                    published_at=_parsed_time(e),
                    external_id=e.get("id") or e.get("guid"),
                    raw={"summary": e.get("summary", ""), "tags":
                         [t.get("term") for t in e.get("tags", [])]},
                )
        finally:
            if own:
                client.close()


# ---------------------------------------------------------------------------
# Default feed registry.
# source_name MUST exist in SOURCE_CATALOG (finrag/config.py).
# ---------------------------------------------------------------------------
DEFAULT_FEEDS: list[FeedSpec] = [
    FeedSpec("SEBI",              "https://www.sebi.gov.in/sebirss.xml", 3.0),
    FeedSpec("MONEYCONTROL",      "https://www.moneycontrol.com/rss/latestnews.xml", 2.0),
    FeedSpec("MONEYCONTROL",      "https://www.moneycontrol.com/rss/business.xml", 2.0),
    FeedSpec("MONEYCONTROL",      "https://www.moneycontrol.com/rss/results.xml", 2.0),
    FeedSpec("MONEYCONTROL",      "https://www.moneycontrol.com/rss/economy.xml", 2.0),
    FeedSpec("ECONOMIC_TIMES",    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",          2.0),
    FeedSpec("ECONOMIC_TIMES",    "https://cfo.economictimes.indiatimes.com/rss/policy",                           2.0),
    FeedSpec("ECONOMIC_TIMES",    "https://cfo.economictimes.indiatimes.com/rss/governance-risk-compliance",       2.0),
    FeedSpec("LIVEMINT",          "https://www.livemint.com/rss/markets",   2.0),
    FeedSpec("LIVEMINT",          "https://www.livemint.com/rss/companies", 2.0),
    FeedSpec("LIVEMINT",          "https://www.livemint.com/rss/industry",  2.0),
    FeedSpec("BUSINESSLINE",      "https://www.thehindubusinessline.com/markets/feeder/default.rss", 2.0),
    FeedSpec("BUSINESSLINE",      "https://www.thehindubusinessline.com/companies/feeder/default.rss", 2.0),
    FeedSpec("BUSINESSLINE",      "https://www.thehindubusinessline.com/portfolio/feeder/default.rss", 2.0),
    FeedSpec("BUSINESSLINE",      "https://www.thehindubusinessline.com/economy/feeder/default.rss", 2.0),
    FeedSpec("BUSINESS_STANDARD", "https://www.business-standard.com/rss/markets-106.rss", 2.0),
    FeedSpec("BUSINESS_STANDARD", "https://www.business-standard.com/rss/companies-101.rss",                        2.0),
    FeedSpec("BUSINESS_STANDARD", "https://www.business-standard.com/rss/companies/quarterly-results-10103.rss",    2.0),
    FeedSpec("BUSINESS_STANDARD", "https://www.business-standard.com/rss/industry-217.rss", 2.0),
    FeedSpec("BUSINESS_STANDARD", "https://www.business-standard.com/rss/economy-102.rss", 2.0),
    FeedSpec("BUSINESS_STANDARD", "https://www.business-standard.com/rss/finance-103.rss", 2.0),
]

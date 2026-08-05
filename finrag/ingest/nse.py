"""
finrag.ingest.nse — NSE corporate announcements scraper.

NSE exposes a JSON API that works without authentication. We pull the last
24 hours of announcements and yield one RawItem per filing.

BSE is intentionally omitted: their API redirects all programmatic requests
to an error page and requires a real browser session (Selenium/Playwright).
NSE coverage is sufficient since major companies are cross-listed.

Title format: "{company}: {category}" so the resolver can match on company name.
Body:         attchmntText (the exchange dissemination text)
URL:          PDF attachment (the actual filing)
"""
from __future__ import annotations

import datetime as dt
from typing import Iterable

from finrag.ingest.base import RawItem, normalize_text
from finrag.ingest.http import HttpClient

_API = "https://www.nseindia.com/api/corporate-announcements"
_DATE_FMT = "%d-%m-%Y"
_DT_FMT = "%d-%b-%Y %H:%M:%S"


def _parse_dt(raw: str) -> dt.datetime | None:
    try:
        return dt.datetime.strptime(raw.strip(), _DT_FMT).replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


class NseAdapter:
    name = "NSE_ANN"

    def fetch(self, client: HttpClient | None = None) -> Iterable[RawItem]:
        own = client is None
        client = client or HttpClient(
            min_interval=2.0,
            # brotli causes decode errors with this endpoint; restrict encoding
            headers={"Accept-Encoding": "gzip, deflate"},
        )
        try:
            today = dt.date.today()
            yesterday = today - dt.timedelta(days=1)
            url = (
                f"{_API}?index=equities"
                f"&from_date={yesterday.strftime(_DATE_FMT)}"
                f"&to_date={today.strftime(_DATE_FMT)}"
            )
            resp = client.get(url)
            for item in resp.json():
                company = normalize_text(item.get("sm_name", ""))
                category = normalize_text(item.get("desc", ""))
                body = normalize_text(item.get("attchmntText", ""))
                if not company:
                    continue
                title = f"{company}: {category}" if category else company
                yield RawItem(
                    source_name=self.name,
                    title=title,
                    url=item.get("attchmntFile"),
                    body=body,
                    published_at=_parse_dt(item.get("an_dt", "")),
                    external_id=str(item.get("seq_id", "")),
                    raw={
                        "symbol": item.get("symbol"),
                        "isin": item.get("sm_isin"),
                        "category": category,
                    },
                )
        finally:
            if own:
                client.close()

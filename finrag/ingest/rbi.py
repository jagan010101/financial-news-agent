"""
finrag.ingest.rbi — RBI press release scraper.

RBI's RSS feed (Rss.aspx) is permanently blocked by an F5 WAF (HTTP 418).
The press release listing page is accessible after cookie-seeding the homepage.
We parse the HTML table directly instead.
"""
from __future__ import annotations

import datetime as dt
from typing import Iterable

from bs4 import BeautifulSoup

from finrag.ingest.base import RawItem, normalize_text
from finrag.ingest.http import HttpClient

_HOME = "https://www.rbi.org.in/"
_LIST = "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx"
_BASE = "https://www.rbi.org.in/Scripts/"


def _parse_date(raw: str) -> dt.datetime | None:
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return dt.datetime.strptime(raw.strip(), fmt).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            pass
    return None


class RbiAdapter:
    name = "RBI"

    def fetch(self, client: HttpClient | None = None) -> Iterable[RawItem]:
        own = client is None
        client = client or HttpClient(
            min_interval=3.0,
            headers={"Referer": _HOME},
        )
        try:
            client.get(_HOME)            # seed F5 session cookies
            resp = client.get(_LIST)
            soup = BeautifulSoup(resp.content, "lxml")

            current_date: dt.datetime | None = None
            for row in soup.select("table tr"):
                cells = row.find_all("td")
                if not cells:
                    continue

                # date header row — single cell, no link
                if len(cells) == 1 and not cells[0].find("a"):
                    current_date = _parse_date(cells[0].get_text())
                    continue

                # press release row — first cell has the link
                link = cells[0].find("a", href=True)
                if not link:
                    continue

                title = normalize_text(link.get_text())
                if not title:
                    continue

                href = link["href"]
                url = href if href.startswith("http") else _BASE + href
                prid = href.split("prid=")[-1] if "prid=" in href else None

                yield RawItem(
                    source_name=self.name,
                    title=title,
                    url=url,
                    published_at=current_date,
                    external_id=prid,
                    raw={"prid": prid},
                )
        finally:
            if own:
                client.close()

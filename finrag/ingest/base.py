"""
finrag.ingest.base — the contract every source adapter implements.

A source adapter's only job: pull recent items from one source and yield
normalized RawItem objects. Persistence, dedup, and logging are handled
centrally (in pipeline.py) so adapters stay thin and uniform.

Design:
  * RawItem is the normalized boundary type between "messy source" and "our DB".
  * content_hash is computed ONCE here from normalized title+body, so the same
    story from two sources collides on the unique constraint and dedups for free.
  * fetch() returns an iterable; adapters never touch the database.
  * HTTP goes through a shared, rate-limited, retrying client (http.py).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Protocol


def normalize_text(s: str | None) -> str:
    """Canonicalize for hashing/dedup: unicode NFKC, collapse whitespace, strip."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def content_hash(title: str | None, body: str | None) -> str:
    """Stable dedup key. Title carries most signal; body disambiguates."""
    basis = normalize_text(title) + "\x1f" + normalize_text(body)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class RawItem:
    source_name: str                       # must match a row in `sources`
    title: str
    url: str | None = None
    body: str | None = None
    published_at: dt.datetime | None = None
    external_id: str | None = None
    raw: dict = field(default_factory=dict)  # original payload, JSONB audit trail

    def hash(self) -> str:
        return content_hash(self.title, self.body)


class SourceAdapter(Protocol):
    name: str                              # source key, matches SOURCE_CATALOG
    def fetch(self) -> Iterable[RawItem]: ...

"""
Seed the database with the source catalogue and your holdings registry.

Idempotent: safe to re-run. Run after applying sql/001_schema.sql:
    python -m scripts.seed
"""
from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert

from finrag.config import SOURCE_CATALOG
from finrag.store.db import Holding, SessionLocal, Source, Subsidiary
from finrag.store.holdings_seed import PORTFOLIO
from finrag.store.subsidiaries_seed import SUBSIDIARIES


def seed_sources(session) -> int:
    rows = [
        dict(id=i, name=n, kind=k, authority_rank=a, poll_seconds=p)
        for (i, n, k, a, p) in SOURCE_CATALOG
    ]
    stmt = insert(Source).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Source.id],
        set_={"name": stmt.excluded.name, "kind": stmt.excluded.kind,
              "authority_rank": stmt.excluded.authority_rank,
              "poll_seconds": stmt.excluded.poll_seconds},
    )
    session.execute(stmt)
    return len(rows)


def seed_holdings(session) -> int:
    stmt = insert(Holding).values(PORTFOLIO)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Holding.isin],
        set_={
            "nse_symbol": stmt.excluded.nse_symbol,
            "bse_code": stmt.excluded.bse_code,
            "legal_name": stmt.excluded.legal_name,
            "common_name": stmt.excluded.common_name,
            "sector": stmt.excluded.sector,
            "industry": stmt.excluded.industry,
            "aliases": stmt.excluded.aliases,
            "weight": stmt.excluded.weight,
        },
    )
    session.execute(stmt)
    return len(PORTFOLIO)


def seed_subsidiaries(session) -> int:
    stmt = insert(Subsidiary).values(SUBSIDIARIES)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Subsidiary.parent_nse_symbol, Subsidiary.subsidiary_name],
        set_={
            "parent_name": stmt.excluded.parent_name,
            "aliases": stmt.excluded.aliases,
            "notes": stmt.excluded.notes,
        },
    )
    session.execute(stmt)
    return len(SUBSIDIARIES)


def main() -> None:
    with SessionLocal() as session:
        ns = seed_sources(session)
        nh = seed_holdings(session)
        nsub = seed_subsidiaries(session)
        session.commit()
        print(f"seeded {ns} sources, {nh} holdings, {nsub} subsidiaries")


if __name__ == "__main__":
    main()

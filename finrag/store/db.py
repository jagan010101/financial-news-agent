"""
finrag.store.db — SQLAlchemy 2.0 models mirroring sql/001_schema.sql.

We keep raw SQL as the source of truth for the schema (DDL in sql/) and use
the ORM for application reads/writes. The models below match the DDL exactly;
a test asserts they stay in sync.
"""
from __future__ import annotations

import datetime as dt

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY, BigInteger, Boolean, ForeignKey, Numeric, SmallInteger, String,
    Text, create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker,
)

from finrag.config import settings


class Base(DeclarativeBase):
    pass


class Holding(Base):
    __tablename__ = "holdings"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    isin: Mapped[str | None] = mapped_column(Text, unique=True)
    nse_symbol: Mapped[str | None] = mapped_column(Text)
    bse_code: Mapped[str | None] = mapped_column(Text)
    legal_name: Mapped[str] = mapped_column(Text, nullable=False)
    common_name: Mapped[str] = mapped_column(Text, nullable=False)
    sector: Mapped[str | None] = mapped_column(Text)
    industry: Mapped[str | None] = mapped_column(Text)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    weight: Mapped[float | None] = mapped_column(Numeric)
    notes: Mapped[str | None] = mapped_column(Text)


class Subsidiary(Base):
    __tablename__ = "subsidiaries"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    parent_nse_symbol: Mapped[str] = mapped_column(Text, nullable=False)
    parent_name: Mapped[str] = mapped_column(Text, nullable=False)
    subsidiary_name: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    notes: Mapped[str | None] = mapped_column(Text)


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    authority_rank: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    poll_seconds: Mapped[int] = mapped_column(default=900)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Article(Base):
    __tablename__ = "articles"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))
    url: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[dt.datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    fetched_at: Mapped[dt.datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    content_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    minhash: Mapped[bytes | None] = mapped_column()
    embedding: Mapped[list[float] | None] = mapped_column(Vector(settings.embed_dim))
    lang: Mapped[str] = mapped_column(Text, default="en")
    raw: Mapped[dict | None] = mapped_column(JSONB)
    event_type: Mapped[str | None] = mapped_column(Text)
    dup_of: Mapped[int | None] = mapped_column(ForeignKey("articles.id"))
    status: Mapped[str] = mapped_column(Text, default="ingested")


class ArticleHolding(Base):
    __tablename__ = "article_holdings"
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True)
    holding_id: Mapped[int] = mapped_column(ForeignKey("holdings.id", ondelete="CASCADE"), primary_key=True)
    match_method: Mapped[str] = mapped_column(Text, nullable=False)
    match_score: Mapped[float | None] = mapped_column(Numeric)


class Score(Base):
    __tablename__ = "scores"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    holding_id: Mapped[int] = mapped_column(ForeignKey("holdings.id", ondelete="CASCADE"))
    direct_relevance: Mapped[int | None] = mapped_column(SmallInteger)
    materiality: Mapped[int | None] = mapped_column(SmallInteger)
    urgency: Mapped[int | None] = mapped_column(SmallInteger)
    credibility: Mapped[int | None] = mapped_column(SmallInteger)
    composite: Mapped[float] = mapped_column(Numeric, nullable=False)
    rule_floor: Mapped[int | None] = mapped_column(SmallInteger)
    event_type: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    scored_at:         Mapped[dt.datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    label_material:    Mapped[bool | None]       = mapped_column(Boolean)
    validation_status: Mapped[str | None]        = mapped_column(Text)
    flag_reasons:      Mapped[list[str] | None]  = mapped_column(ARRAY(Text))
    finbert_label:     Mapped[str | None]        = mapped_column(Text)
    finbert_score:     Mapped[float | None]      = mapped_column(Numeric)


# --- engine / session ---------------------------------------------------------
engine = create_engine(settings.database_url, future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)

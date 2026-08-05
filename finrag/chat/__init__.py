"""finrag.chat — conversational RAG over the ingested article corpus.

Retrieval is deliberately NOT restricted to portfolio holdings: the wire/
aggregator sources ingest broad market news, so the article table already
covers companies and sectors outside PORTFOLIO. This package answers
open-ended questions ("what's happening with X", "how's the IT sector"),
while layering in portfolio awareness (weights, sector, recent scores) when
the question touches a holding.
"""

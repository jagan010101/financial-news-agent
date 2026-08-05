"""
finrag.score.dispute_log — audit trail for premium-LLM dispute resolution.

Every time the deterministic validator flags/rejects a normal-tier (Ollama)
score and a premium judge (Groq/Google) re-scores it, one row is appended
here: the normal-tier verdict, the premium verdict, and which one won (always
the premium one — see score/pipeline.py). Plain stdlib csv, append-only, so
it survives across runs as a running record.
"""
from __future__ import annotations

import csv
import datetime as dt
import pathlib

OUT_PATH = pathlib.Path(__file__).parent.parent.parent / "logs" / "dispute_verdicts.csv"

COLUMNS = [
    "timestamp", "article_id", "holding_id", "holding_name",
    "dispute_reasons",
    "normal_provider", "normal_event_type", "normal_composite",
    "normal_direct_relevance", "normal_materiality", "normal_urgency", "normal_credibility",
    "premium_provider", "premium_event_type", "premium_composite",
    "premium_direct_relevance", "premium_materiality", "premium_urgency", "premium_credibility",
    "final_composite", "final_event_type", "premium_rationale",
]


def record_dispute(
    *,
    article_id: int,
    holding_id: int,
    holding_name: str,
    dispute_reasons: list[str],
    normal_provider: str,
    normal_dims: dict,
    normal_composite: float,
    premium_provider: str,
    premium_dims: dict,
    premium_composite: float,
    final_composite: float,
    final_event_type: str,
    out_path: pathlib.Path | None = None,
) -> None:
    """Append one row to the dispute-verdicts CSV, creating it with a header
    on first use. Never raises into the scoring path — logging failures here
    must not lose the actual score."""
    path = out_path or OUT_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            if is_new:
                w.writeheader()
            w.writerow({
                "timestamp": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
                "article_id": article_id,
                "holding_id": holding_id,
                "holding_name": holding_name,
                "dispute_reasons": "|".join(dispute_reasons),
                "normal_provider": normal_provider,
                "normal_event_type": normal_dims.get("event_type"),
                "normal_composite": normal_composite,
                "normal_direct_relevance": normal_dims.get("direct_relevance"),
                "normal_materiality": normal_dims.get("materiality"),
                "normal_urgency": normal_dims.get("urgency"),
                "normal_credibility": normal_dims.get("credibility"),
                "premium_provider": premium_provider,
                "premium_event_type": premium_dims.get("event_type"),
                "premium_composite": premium_composite,
                "premium_direct_relevance": premium_dims.get("direct_relevance"),
                "premium_materiality": premium_dims.get("materiality"),
                "premium_urgency": premium_dims.get("urgency"),
                "premium_credibility": premium_dims.get("credibility"),
                "final_composite": final_composite,
                "final_event_type": final_event_type,
                "premium_rationale": premium_dims.get("rationale", ""),
            })
    except OSError:
        pass

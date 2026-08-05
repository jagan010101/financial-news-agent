"""
Score resolved articles.

    python -m scripts.score          # score all status='resolved' articles

Requires a judge backend reachable per .env (LLM_PROVIDER)
Writes scores (per-dimension + composite + rule_floor + rationale + model tag)
and advances articles to status='scored'. Items above SCORE_THRESHOLD are
counted as report-eligible.
"""
from finrag.score.pipeline import run

if __name__ == "__main__":
    s = run()
    print(f"articles={s['articles']} scores={s['scores']} "
          f"above_threshold={s['above_threshold']} errors={s['errors']}")

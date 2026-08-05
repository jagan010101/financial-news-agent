"""
Run entity resolution over ingested articles.

    python -m scripts.resolve          # resolve all status='ingested' articles

Links matched articles to holdings (article_holdings) and advances status to
'resolved' (-> scoring) or 'irrelevant' (the pre-filter drop).
"""
from finrag.process.pipeline import run

if __name__ == "__main__":
    s = run()
    print(f"processed={s['processed']} resolved={s['resolved']} "
          f"irrelevant={s['irrelevant']} links={s['links']}")

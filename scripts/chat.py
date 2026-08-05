"""
Chat with the retrieved news corpus + your portfolio.

    python -m scripts.chat                      # interactive REPL
    python -m scripts.chat "What's happening with Reliance?"   # one-shot

Retrieval is corpus-wide (any company, any industry) — the article table
isn't limited to portfolio holdings. Mentioning a holding by name/ticker
layers in its weight, sector, and recent scored coverage automatically.
Type /quit or Ctrl-D to exit the REPL.
"""
from __future__ import annotations

import argparse
import logging
import sys


def _print_answer(result) -> None:
    print(f"\n{result.text}\n")
    if result.matched_holding is not None:
        h = result.matched_holding
        print(f"(portfolio holding matched: {h.common_name} / {h.nse_symbol})")
    if result.sources:
        print("sources:")
        for i, s in enumerate(result.sources, 1):
            date = s.published_at.strftime("%Y-%m-%d") if s.published_at else "undated"
            print(f"  [{i}] {s.title} — {s.source or 'unknown'}, {date}")
            if s.url:
                print(f"      {s.url}")
    print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="*", help="ask one question and exit")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING,
                         format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    from finrag.chat.bot import answer

    if args.question:
        result = answer(" ".join(args.question))
        _print_answer(result)
        return

    print("finrag chat — ask about any company, sector, or your portfolio. /quit to exit.\n")
    history: list[dict] = []
    while True:
        try:
            question = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question:
            continue
        if question in ("/quit", "/exit"):
            break
        try:
            result = answer(question, history=history)
        except Exception as e:
            print(f"error: {e}\n", file=sys.stderr)
            continue
        _print_answer(result)
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": result.text})


if __name__ == "__main__":
    main()

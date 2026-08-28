#!/usr/bin/env python3
"""tools/report.py - what Upsell AI did, and what it cost.

    make report
    python3 tools/report.py [--since 2026-09-01]

Reads core.store: item counts by kind/status, the euro value sitting in the
review queue and already accepted, the LLM spend (run-narrative + the
coach), and how many coach suggestions are waiting for a decision. This is
the metric behind the roster's `output`/`roi` claims - see docs/benefits.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, load_settings  # noqa: E402
from core.store import STATUSES, Store  # noqa: E402
from tools import coach  # noqa: E402


def _counts_by_kind(store: Store) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    rows = store.db.execute(
        "SELECT kind, review_status, COUNT(*) AS n FROM items GROUP BY kind, review_status")
    for row in rows.fetchall():
        out.setdefault(row["kind"], {})[row["review_status"]] = row["n"]
    return out


def _accepted_value(store: Store, kind: str) -> float:
    total = 0.0
    for item in store.list_items(status=list(STATUSES), kind=kind, limit=10000):
        response = (item.payload or {}).get("response") or {}
        if response.get("outcome") == "accepted":
            total += float(response.get("accepted_value") or item.payload.get("total_delta", 0) or 0)
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", default=None, help="ISO timestamp - only count usage after this")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    store.migrate(coach.SCHEMA)
    try:
        counts = _counts_by_kind(store)
        print(f"Upsell AI report - {settings.hotel.name} ({settings.mode})\n")
        for kind in ("outreach", "upgrade", "upgrade_execute"):
            by_status = counts.get(kind, {})
            if not by_status:
                continue
            waiting = sum(n for s, n in by_status.items()
                         if s in ("pending_review", "needs_human"))
            print(f"  {kind:<16} " + ", ".join(f"{s}={n}" for s, n in sorted(by_status.items())))
            if waiting:
                print(f"                   {waiting} waiting for a human")

        outreach_value = _accepted_value(store, "outreach")
        upgrade_value = _accepted_value(store, "upgrade")
        print(f"\n  Accepted so far: {settings.hotel.currency} {outreach_value:,.0f} "
             f"ancillary + {settings.hotel.currency} {upgrade_value:,.0f} upgrade surcharge")

        suggestions = coach.list_suggestions(store, status="new")
        print(f"\n  Coach: {len(suggestions)} suggestion(s) waiting - `python3 tools/coach.py list`")

        usage = store.usage_totals(since=args.since)
        print(f"\n  LLM usage{f' since {args.since}' if args.since else ''}: {usage['calls']} "
             f"call(s), {usage['input_tokens']} in / {usage['output_tokens']} out tokens, "
             f"${usage['cost_usd']:.4f}")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())

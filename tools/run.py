#!/usr/bin/env python3
"""tools/run.py - Upsell AI's main loop: draft Track A and Track B, then log.

    python3 tools/run.py --once
    python3 tools/run.py --watch
    python3 tools/run.py --once --only outreach
    python3 tools/run.py --once --only upgrade
    python3 tools/run.py --once --dry-run
    python3 tools/run.py --once --as-of 2026-09-01

One pass: for each enabled track, read the arrivals book, dedup, decide,
draft, and queue for review - Track A and Track B never send on their own
(workflows/80-review.md and docs/safety.md cover the review queue and the
shadow/live switch). The only LLM call is a cosmetic one-line summary at the
end (`prompts/run-narrative.md`), which never blocks a run.

`--dry-run` computes everything and writes nothing at all: no item rows (the
scans already skip their own writes - see tools/outreach.py / tools/upgrade.py),
no `runs` row, no expiry sweep, and no cosmetic narrative line (which would
otherwise log an LLM-usage event even on the mock provider). Running it twice
in a row leaves the database exactly as it was, with no risk of a duplicate
row.

Exit codes: 0 ok, 3 waiting on an `interactive` answer for the narrative
(the scans themselves already completed and are queued either way; never
raised under --dry-run, since the narrative is skipped entirely), 1 a real
error.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import get_pms  # noqa: E402
from core.adapters.base import AdapterError  # noqa: E402
from core.config import ConfigError, load_settings  # noqa: E402
from core.llm import LLMError, LLMPendingInteractive, complete  # noqa: E402
from core.log import Run, get_logger  # noqa: E402
from core.store import Store, StoreError  # noqa: E402
from core.templates import build_prompt  # noqa: E402
from tools import coach, outreach, upgrade  # noqa: E402

log = get_logger("run")
SCHEMAS_DIR = REPO_ROOT / "prompts" / "schemas"


def narrate(settings, store, stats: dict, *, provider: str | None = None) -> str:
    """One cosmetic summary line - never required, never blocks a run."""
    o, u = stats.get("outreach", {}), stats.get("upgrade", {})
    fallback = (f"{o.get('drafted', 0)} outreach email(s) drafted, "
               f"{u.get('sent', 0)} upgrade offer(s) drafted, {u.get('held', 0)} held for approval.")
    schema = json.loads((SCHEMAS_DIR / "run-narrative.json").read_text(encoding="utf-8"))
    prompt = build_prompt("run-narrative", settings=settings, item=stats,
                          fixture_id="scan-summary")
    result = complete("run-narrative", prompt, schema, settings=settings,
                      provider=provider, store=store)
    return (result.data or {}).get("note") or fallback


def one_pass(settings, store, *, limit: int | None, provider: str | None, only: str,
            today: date, dry_run: bool) -> tuple[int, dict]:
    stats: dict = {}
    # --dry-run must write nothing at all - not even a `runs` row or a
    # cosmetic LLM-usage event. core.log.Run and core.llm.complete() both
    # write unconditionally whenever they are given a store, so under
    # --dry-run this never passes `store` into Run(), never sweeps expired
    # offers (an UPDATE), and never calls narrate() at all. See the module
    # docstring; verified by running `--dry-run` twice back to back.
    with Run("upsell", settings, None if dry_run else store) as run:
        pms = get_pms(settings)
        # Track B first: an open upgrade item has to exist before Track A can
        # see it and skip the guest for real - see docs/how-it-works.md
        # "Design decisions" #3.
        if only in ("all", "upgrade"):
            stats["upgrade"] = upgrade.scan(settings, store, pms, today=today,
                                            limit=limit, dry_run=dry_run)
            stats["expired"] = upgrade.sweep_expired(store) if not dry_run else []
        if only in ("all", "outreach"):
            stats["outreach"] = outreach.scan(settings, store, pms, today=today,
                                              limit=limit, dry_run=dry_run)
        run.stats = dict(stats)
        if dry_run:
            return 0, stats
        try:
            stats["narrative"] = narrate(settings, store, stats, provider=provider)
        except LLMPendingInteractive as exc:
            run.stats = dict(stats)
            print(str(exc))
            return 3, stats
    return 0, stats


def _print_summary(stats: dict, mode: str) -> None:
    note = stats.get("narrative")
    if note:
        print(f"\n{note}")
    o, u = stats.get("outreach", {}), stats.get("upgrade", {})
    parts = []
    if "outreach" in stats:
        parts.append(f"outreach: {o.get('drafted', 0)} drafted, {o.get('skipped', 0)} skipped")
    if "upgrade" in stats:
        # "drafted", not "sent" - a scan only ever queues an offer for
        # approval, it never sends one (see tools/upgrade.py cmd_scan, which
        # uses the same wording for the same numbers).
        parts.append(f"upgrade: {u.get('sent', 0)} drafted, {u.get('held', 0)} held, "
                     f"{u.get('skipped', 0)} skipped")
    if stats.get("expired"):
        parts.append(f"{len(stats['expired'])} offer(s) lapsed")
    print(f"UPSELL RUN OK - {' | '.join(parts)} ({mode})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--once", action="store_true", help="run a single pass (default)")
    mode_group.add_argument("--watch", action="store_true",
                            help="keep running on the configured interval")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute everything, write nothing, even in live mode")
    parser.add_argument("--limit", type=int, default=None, help="max items per track, per pass")
    parser.add_argument("--provider", default=None, help="override llm.provider for this run")
    parser.add_argument("--only", choices=["all", "outreach", "upgrade"], default="all")
    parser.add_argument("--as-of", default=None, help="override today, YYYY-MM-DD (testing)")
    parser.add_argument("--poll-seconds", type=int, default=None,
                        help="override the --watch interval (default: agent.yaml or 3600)")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(provider=args.provider, dry_run=args.dry_run)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    today = date.fromisoformat(args.as_of) if args.as_of else date.today()
    store = Store(settings)
    store.migrate(coach.SCHEMA)
    try:
        if args.watch:
            poll_seconds = args.poll_seconds or int(settings.agent_get("poll_seconds", 3600))
            while True:
                code, stats = one_pass(settings, store, limit=args.limit, provider=args.provider,
                                       only=args.only, today=today, dry_run=args.dry_run)
                _print_summary(stats, settings.mode)
                if code != 0:
                    return code
                time.sleep(poll_seconds)
        code, stats = one_pass(settings, store, limit=args.limit, provider=args.provider,
                               only=args.only, today=today, dry_run=args.dry_run)
        _print_summary(stats, settings.mode)
        return code
    except AdapterError as exc:
        print(f"integration error: {exc}", file=sys.stderr)
        print("Run `make doctor` to see what is missing and how to fix it.", file=sys.stderr)
        return 1
    except StoreError as exc:
        print(f"cannot do that: {exc}", file=sys.stderr)
        return 1
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())

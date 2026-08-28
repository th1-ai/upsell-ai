#!/usr/bin/env python3
"""tools/coach.py - Email Optimizer / Coach AI, weekly.

    python3 tools/coach.py run
    python3 tools/coach.py list [--status new]
    python3 tools/coach.py accept <id>
    python3 tools/coach.py dismiss <id>

Every `approve` / `edit` / `reject` in tools/review.py already writes a
before/after pair to core.store's `learnings` table (core/review.py,
unchanged). Weekly, this clusters new learnings by what they were for, asks
one question per cluster, and lets a human decide. Nothing is auto-applied -
see docs/how-it-works.md "The coach".

On `llm.provider: interactive`, `run` can park one prompt per cluster in
`data/pending/` and exit 3 - see CLAUDE.md "The interactive provider". That
is a pause, not a failure: every cluster still gets its prompt file written
in the same pass, so a hotel can answer them all and re-run once.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, Settings, load_settings, repo_root  # noqa: E402
from core.llm import LLMError, LLMPendingInteractive, LLMSchemaError, complete  # noqa: E402
from core.log import Run, get_logger  # noqa: E402
from core.store import Store, StoreError, utcnow  # noqa: E402
from core.templates import build_prompt  # noqa: E402

SCHEMA = """
CREATE TABLE IF NOT EXISTS coach_suggestions (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ts            TEXT NOT NULL,
  applied_to    TEXT,
  cluster_size  INTEGER NOT NULL DEFAULT 1,
  suggestion    TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'new',
  source_items  TEXT
);
"""

FALLBACK = ("Add a rule to config/agent.yaml or a fact to knowledge/property.md so this "
           "pattern is handled automatically next time.")
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "prompts" / "schemas" / "coach-suggest.json"

log = get_logger("coach", quiet=True)


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# clustering - a real groupby, unlike the source demo (see docs/how-it-works.md)
# --------------------------------------------------------------------------
def cluster_learnings(learnings: list[dict]) -> dict[str, list[dict]]:
    clusters: dict[str, list[dict]] = {}
    for row in learnings:
        key = row.get("applied_to") or "general"
        clusters.setdefault(key, []).append(row)
    return clusters


def _insert_suggestion(store: Store, applied_to: str, cluster_size: int, suggestion: str,
                       source_items: list[str]) -> int:
    store.db.execute(
        "INSERT INTO coach_suggestions (ts, applied_to, cluster_size, suggestion, "
        "status, source_items) VALUES (?,?,?,?,?,?)",
        (utcnow(), applied_to, cluster_size, suggestion, "new",
         json.dumps(source_items, ensure_ascii=False)))
    row = store.db.execute("SELECT last_insert_rowid() AS id").fetchone()
    return int(row["id"])


def run(settings: Settings, store: Store, provider: str | None = None) -> dict:
    """Cluster new learnings and produce one suggestion per cluster.

    A pending `interactive` prompt is never swallowed into the generic
    `FALLBACK` string (`LLMPendingInteractive` is not an `LLMError` - see
    core/llm.py). Every cluster still gets its own prompt file written even
    if an earlier one already pended this run, so a hotel can answer several
    at once (CLAUDE.md, "The interactive provider").

    The cursor (`coach:last_run`) only advances when nothing is left
    pending or errored, so a pending or schema-invalid cluster's learnings
    are re-offered next run rather than skipped forever. Accepted trade-off:
    if some clusters already succeeded this run while another pended,
    re-running after you answer regenerates a *new* prompt for the ones that
    already succeeded too - the interactive provider deletes an answer file
    the moment it is used, so an unresolved batch has no "half done" state
    to resume from. Worst case is a human answering the same question
    twice, never a crash or a silently dropped suggestion.

    A schema-invalid interactive answer (`LLMSchemaError`) is a human
    mistake, not a provider failure, and must never be swallowed into
    `FALLBACK` the way a real provider error (rate limit, unreachable API)
    is - see SIMULATION.md round 2 finding 2. It is surfaced in
    `stats["errors"]` exactly the way `tools/run.py`'s identical scenario
    propagates `LLMSchemaError` all the way to `main()`'s `except LLMError`.
    Nothing here deletes or renames the pending `.prompt.md` / `.schema.json`
    / `.answer.json` files on a schema error - `core.llm._interactive`
    already leaves them untouched when validation fails - so the operator
    can fix `data/pending/<id>.answer.json` in place and re-run.
    """
    store.migrate(SCHEMA)
    with Run("coach", settings, store) as coach_run:
        since = store.get_cursor("coach:last_run")
        learnings = store.list_learnings(limit=500)
        if since:
            learnings = [row for row in learnings if row["ts"] > since]
        clusters = cluster_learnings(learnings)

        stats = {"learnings_seen": len(learnings), "clusters": len(clusters),
                 "suggestions": 0, "pending": [], "errors": []}
        schema = _schema()
        for applied_to, rows in clusters.items():
            examples = [{"before": r["before"][:400], "after": r["after"][:400],
                        "lesson": r["lesson"]} for r in rows[:5]]
            prompt = build_prompt("coach-suggest", settings=settings,
                                  item={"applied_to": applied_to, "cluster_size": len(rows),
                                       "examples": examples},
                                  fixture_id=f"cluster-{applied_to}")
            try:
                result = complete("coach-suggest", prompt, schema, settings=settings,
                                 provider=provider, store=store)
                suggestion = (result.data or {}).get("suggestion") or FALLBACK
            except LLMPendingInteractive as exc:
                stats["pending"].append(str(exc))
                log.info("suggestion pending", applied_to=applied_to, cluster_size=len(rows))
                continue
            except LLMSchemaError as exc:
                stats["errors"].append(f"{applied_to}: {exc}")
                log.warn("suggestion answer invalid", applied_to=applied_to,
                         cluster_size=len(rows), error=str(exc))
                continue
            except LLMError:
                suggestion = FALLBACK
            _insert_suggestion(store, applied_to, len(rows), suggestion,
                              [str(r.get("source_item")) for r in rows if r.get("source_item")])
            stats["suggestions"] += 1
            log.info("suggestion written", applied_to=applied_to, cluster_size=len(rows))
        if not stats["pending"] and not stats["errors"]:
            store.set_cursor("coach:last_run", utcnow())
        coach_run.stats = dict(stats)
    return stats


# --------------------------------------------------------------------------
# a human decides - nothing here changes engine behavior automatically
# --------------------------------------------------------------------------
def list_suggestions(store: Store, status: str | None = None) -> list[dict]:
    store.migrate(SCHEMA)
    if status:
        rows = store.db.execute(
            "SELECT * FROM coach_suggestions WHERE status=? ORDER BY ts DESC", (status,))
    else:
        rows = store.db.execute("SELECT * FROM coach_suggestions ORDER BY ts DESC")
    return [dict(r) for r in rows.fetchall()]


def _rules_path() -> Path:
    return repo_root() / "knowledge" / "rules.md"


def _append_rule(suggestion: str, applied_to: str) -> Path:
    path = _rules_path()
    if not path.exists():
        example = repo_root() / "knowledge" / "rules.example.md"
        path.write_text(example.read_text(encoding="utf-8") if example.exists()
                        else "# Coach suggestions\n\nAccepted suggestions land here.\n",
                        encoding="utf-8")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"\n- ({applied_to}) {suggestion}\n")
    return path


def accept(store: Store, suggestion_id: int) -> dict:
    store.migrate(SCHEMA)
    row = store.db.execute("SELECT * FROM coach_suggestions WHERE id=?",
                           (suggestion_id,)).fetchone()
    if row is None:
        raise KeyError(f"no suggestion {suggestion_id}")
    data = dict(row)
    store.db.execute("UPDATE coach_suggestions SET status='accepted' WHERE id=?",
                     (suggestion_id,))
    path = _append_rule(data["suggestion"], data["applied_to"] or "general")
    data["rules_path"] = str(path)
    log.info("suggestion accepted", suggestion_id=suggestion_id, rules_path=str(path))
    return data


def dismiss(store: Store, suggestion_id: int) -> None:
    store.migrate(SCHEMA)
    store.db.execute("UPDATE coach_suggestions SET status='dismissed' WHERE id=?",
                     (suggestion_id,))
    log.info("suggestion dismissed", suggestion_id=suggestion_id)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def cmd_run(store, settings, args) -> int:
    stats = run(settings, store, provider=args.provider)
    print(f"coach: {stats['learnings_seen']} new learning(s) in {stats['clusters']} "
         f"cluster(s), {stats['suggestions']} suggestion(s) written. "
         "`python3 tools/coach.py list` to read them.")
    if stats["errors"]:
        print(f"\n{len(stats['errors'])} answer(s) did not match the schema - fix "
             "the `.answer.json` file named below and re-run "
             "`python3 tools/coach.py run`:\n", file=sys.stderr)
        for message in stats["errors"]:
            print(f"error: {message}", file=sys.stderr)
    if stats["pending"]:
        print(f"\n{len(stats['pending'])} prompt(s) waiting for your answer:\n")
        for message in stats["pending"]:
            print(message)
    if stats["errors"]:
        return 1
    if stats["pending"]:
        return 3
    return 0


def cmd_list(store, args) -> int:
    rows = list_suggestions(store, status=args.status)
    if not rows:
        print("No suggestions yet - resolve some edits in the review queue, then "
             "`python3 tools/coach.py run`.")
        return 0
    for r in rows:
        print(f"  #{r['id']:<4} {r['status']:<10} ({r['applied_to']}, "
             f"{r['cluster_size']} edit(s)): {r['suggestion']}")
    return 0


def cmd_accept(store, args) -> int:
    try:
        data = accept(store, args.id)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"accepted #{args.id} - appended to {data['rules_path']}")
    return 0


def cmd_dismiss(store, args) -> int:
    dismiss(store, args.id)
    print(f"dismissed #{args.id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="cluster new learnings, write suggestions")
    p_run.add_argument("--provider", default=None)

    p_list = sub.add_parser("list", help="show suggestions")
    p_list.add_argument("--status", default=None, choices=["new", "accepted", "dismissed"])

    p_accept = sub.add_parser("accept", help="append a suggestion to knowledge/rules.md")
    p_accept.add_argument("id", type=int)

    p_dismiss = sub.add_parser("dismiss", help="discard a suggestion")
    p_dismiss.add_argument("id", type=int)

    args = parser.parse_args(argv)
    try:
        settings = load_settings(provider=getattr(args, "provider", None))
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    try:
        if args.command == "run":
            return cmd_run(store, settings, args)
        if args.command == "list":
            return cmd_list(store, args)
        if args.command == "accept":
            return cmd_accept(store, args)
        if args.command == "dismiss":
            return cmd_dismiss(store, args)
        parser.error(f"unknown command {args.command}")
        return 2
    except StoreError as exc:
        print(f"cannot do that: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())

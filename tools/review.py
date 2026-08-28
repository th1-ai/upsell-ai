#!/usr/bin/env python3
"""tools/review.py - work the review queue: list / show / approve / edit / reject / send.

    python3 tools/review.py list [--status pending_review] [--kind upgrade]
    python3 tools/review.py show <id>
    python3 tools/review.py approve <id> [--note "..."]
    python3 tools/review.py edit <id> --body-file draft.txt [--subject "..."] [--note "..."]
    python3 tools/review.py reject <id> --reason "wrong tone"
    python3 tools/review.py retry <id>          # re-queue a failed send
    python3 tools/review.py send [--kind outreach|upgrade|upgrade_execute]
    python3 tools/review.py stale               # go-live step, see workflows/90-go-live.md

Three item kinds share this one queue: ``outreach`` and ``upgrade`` drafts are
plain emails; ``upgrade_execute`` is the guarded PMS write + confirmation
created by ``tools/upgrade.py respond --outcome accept`` once a guest has said
yes. ``send`` dispatches each claimed item by its ``kind`` - see
docs/how-it-works.md. Only this tool writes `approved` / `edited` / `rejected`
/ `stale`; only `send` (here) writes `sending` / `sent`. Nothing here bypasses
`mode: shadow`: every send in `send` is guarded by
`core.review.assert_write_allowed`, which blocks every send while shadow is
on, approved or not - see docs/safety.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import get_email, get_pms  # noqa: E402
from core.adapters.base import AdapterError  # noqa: E402
from core.config import ConfigError, load_settings  # noqa: E402
from core.log import get_logger  # noqa: E402
from core.review import (WriteBlocked, approve, edit, list_queue, reject,  # noqa: E402
                         retry, show, stale_backlog)
from core.store import Store, StoreError  # noqa: E402
from tools.upgrade import execute_one  # noqa: E402

log = get_logger("review", quiet=True)


def _print_item_line(item) -> None:
    payload = item.payload or {}
    if item.kind in ("upgrade", "upgrade_execute"):
        detail = f"{payload.get('from_room', '?')} -> {payload.get('to_room', '?')}"
    else:
        detail = payload.get("room_type", "")
    who = payload.get("guest_name") or payload.get("external_ref") or "?"
    # `item.is_sample` is set by core (core/store.py) for anything read
    # through a mock adapter outside `make demo` - see docs/integrations.md
    # "Sample data is labelled".
    marker = "  [SAMPLE DATA]" if item.is_sample else ""
    print(f"  {item.id}  {item.review_status:<14} {item.kind:<16} {who[:22]:<22} "
         f"{detail[:28]}{marker}")


def cmd_list(store, args) -> int:
    items = list_queue(store, status=args.status, kind=args.kind, limit=args.limit)
    if not items:
        print("Nothing is waiting for you.")
        return 0
    print(f"{len(items)} item(s) waiting:\n")
    for item in items:
        _print_item_line(item)
    print("\nRun `python3 tools/review.py show <id>` for the full draft.")
    return 0


def cmd_show(store, args) -> int:
    try:
        detail = show(store, args.id)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if (detail["item"].get("payload") or {}).get("_sample"):
        print("[SAMPLE DATA] this item was read through a mock adapter, not your "
             "property - see docs/integrations.md.\n")
    print(json.dumps(detail, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_approve(store, args) -> int:
    item = approve(store, args.id, note=args.note or "")
    log.info("approved", item_id=item.id, kind=item.kind)
    print(f"approved {item.id} - now in the send queue")
    return 0


def cmd_edit(store, args) -> int:
    item = store.get_item(args.id)
    if item is None:
        print(f"error: no item {args.id}", file=sys.stderr)
        return 1
    try:
        body = Path(args.body_file).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot read {args.body_file}: {exc}", file=sys.stderr)
        return 1
    new_draft = dict(item.draft or {})
    new_draft["body"] = body
    if args.subject:
        new_draft["subject"] = args.subject
    edit(store, args.id, new_draft, note=args.note or "")
    log.info("edited", item_id=item.id, kind=item.kind)
    print(f"edited {item.id} - now in the send queue")
    return 0


def cmd_reject(store, args) -> int:
    item = reject(store, args.id, reason=args.reason or "")
    log.info("rejected", item_id=item.id, kind=item.kind, reason=args.reason or "")
    print(f"rejected {item.id}")
    return 0


def cmd_retry(store, args) -> int:
    item = retry(store, args.id)
    log.info("retry queued", item_id=item.id, kind=item.kind)
    print(f"queued {item.id} for another send attempt")
    return 0


def cmd_send(store, settings, args) -> int:
    items = store.list_items(status=["approved", "edited"], kind=args.kind, limit=args.limit)
    if not items:
        print("Nothing approved or edited is waiting to send.")
        return 0
    email = get_email(settings)
    pms = get_pms(settings)
    today = date.today()
    sent, failed = 0, 0
    for item in items:
        claimed = store.transition(item.id, "sending", actor="agent", detail={"claim": True})
        try:
            if claimed.kind == "upgrade_execute":
                execute_one(settings, store, pms, email, claimed, today)
            else:
                draft = claimed.draft or {}
                payload = claimed.payload or {}
                to = payload.get("guest_email") or payload.get("from") or payload.get("from_email")
                result = email.send(to, draft.get("subject", ""), draft.get("body", ""),
                                    item=claimed)
                store.mark_sent(claimed.id, result.get("message_id"))
        except WriteBlocked as exc:
            # Not a failure: the mode blocked it. The approval stands so the
            # item is still queued and ready the moment you go live - it does
            # NOT land in `failed` (see core/store.py TRANSITIONS: "sending"
            # -> "approved" is the shadow-block path; "failed" means a real
            # send failure, below). See SIMULATION.md round 2 finding 1.
            store.transition(item.id, "approved", "agent", {"blocked": str(exc)[:200]})
            log.info("send blocked", item_id=item.id, kind=item.kind, reason=str(exc))
            print(f"blocked {item.id} (approval kept): {exc}")
            failed += 1
            continue
        except Exception as exc:  # noqa: BLE001 - record and move on, never crash the batch
            store.mark_send_failed(item.id, str(exc))
            log.warn("send failed", item_id=item.id, kind=item.kind, error=str(exc))
            print(f"failed {item.id}: {exc}")
            failed += 1
            continue
        log.info("sent", item_id=item.id, kind=item.kind)
        print(f"sent {item.id} ({item.kind})")
        sent += 1
    print(f"\n{sent} sent, {failed} failed.")
    return 0 if failed == 0 else 1


def cmd_stale(store, args) -> int:
    moved = stale_backlog(store)
    log.info("marked stale", count=len(moved), item_ids=moved)
    print(f"marked {len(moved)} item(s) stale. Nothing from before go-live will be sent.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="what is waiting for a human")
    p_list.add_argument("--status", default=None)
    p_list.add_argument("--kind", default=None,
                        choices=["outreach", "upgrade", "upgrade_execute"])
    p_list.add_argument("--limit", type=int, default=50)

    p_show = sub.add_parser("show", help="full detail for one item")
    p_show.add_argument("id")

    p_approve = sub.add_parser("approve", help="approve the draft unchanged")
    p_approve.add_argument("id")
    p_approve.add_argument("--note", default="")

    p_edit = sub.add_parser("edit", help="rewrite the draft, then queue it")
    p_edit.add_argument("id")
    p_edit.add_argument("--body-file", required=True)
    p_edit.add_argument("--subject", default=None)
    p_edit.add_argument("--note", default="")

    p_reject = sub.add_parser("reject", help="discard the draft")
    p_reject.add_argument("id")
    p_reject.add_argument("--reason", "--note", dest="reason", default="")

    p_retry = sub.add_parser("retry", help="re-queue a failed send")
    p_retry.add_argument("id")

    p_send = sub.add_parser("send", help="send everything approved or edited")
    p_send.add_argument("--limit", type=int, default=20)
    p_send.add_argument("--kind", default=None,
                        choices=["outreach", "upgrade", "upgrade_execute"])

    sub.add_parser("stale", help="go-live step: mark everything still un-sent as stale "
                                 "(the shadow-era queue was never sent and is out of date)")

    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    try:
        if args.command == "list":
            return cmd_list(store, args)
        if args.command == "show":
            return cmd_show(store, args)
        if args.command == "approve":
            return cmd_approve(store, args)
        if args.command == "edit":
            return cmd_edit(store, args)
        if args.command == "reject":
            return cmd_reject(store, args)
        if args.command == "retry":
            return cmd_retry(store, args)
        if args.command == "send":
            return cmd_send(store, settings, args)
        if args.command == "stale":
            return cmd_stale(store, args)
        parser.error(f"unknown command {args.command}")
        return 2
    except AdapterError as exc:
        print(f"integration error: {exc}", file=sys.stderr)
        print("Run `make doctor` to see what is missing and how to fix it.", file=sys.stderr)
        return 1
    except StoreError as exc:
        print(f"cannot do that: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())

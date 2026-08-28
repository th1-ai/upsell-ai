#!/usr/bin/env python3
"""tools/outreach.py - Track A, "The Welcomer": pre-arrival outreach.

    python3 tools/outreach.py scan [--as-of YYYY-MM-DD] [--limit N] [--dry-run]
    python3 tools/outreach.py respond <item-id> [--accept OFFER_ID,...] [--note "..."]

Deliberately LLM-free - see docs/how-it-works.md. `scan()` is also called
directly by tools/run.py; the CLI here is for running Track A on its own and
for `respond`, which a human runs after reading a guest's reply.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import get_pms  # noqa: E402
from core.adapters.base import AdapterError, Reservation  # noqa: E402
from core.config import ConfigError, Settings, load_settings  # noqa: E402
from core.log import get_logger  # noqa: E402
from core.store import Item, Store, StoreError  # noqa: E402
from tools.domain import (days_out, iso, load_offers, match_offers, nights,  # noqa: E402
                          offer_value, price_label)

CONFIG_KEY = "subagents.pre_arrival_outreach"
log = get_logger("outreach", quiet=True)

#: fixed reasons per technical send-status, mirroring tools/upgrade.py's
#: STATE_SKIP_REASON - see docs/how-it-works.md step 5 and SIMULATION.md
#: round 2 finding 1. MUST cover every core.store.STATUSES value except
#: "new" (a row still "new" is one this scan created and then never
#: finished - retried below, not skipped forever). `.get()` falls back to a
#: generic-but-readable reason for anything not listed, so an unrecognised
#: status is always skipped with a reason, never silently re-drafted.
STATE_SKIP_REASON = {
    "dispatched": "already queued for a decision - one email per stay",
    "pending_review": "outreach already out - one email per stay",
    "needs_human": "outreach already out, held for a human OK - one email per stay",
    "approved": "outreach approved and about to send - one email per stay",
    "edited": "outreach approved and about to send - one email per stay",
    "sending": "outreach is sending right now - one email per stay",
    "sent": "outreach already sent - waiting on the guest's reply",
    "stale": "let the outreach lapse - not asked twice",
    "failed": "previous outreach failed to send - run `python3 tools/review.py "
             "retry <id>` before this will try again",
    "rejected": "outreach was rejected - not re-drafting automatically",
    "auto_sent": "already auto-sent",
    "skipped": "already marked skipped",
}


def _cfg(settings: Settings) -> dict:
    defaults = {
        "enabled": True, "outreach_window_days": 21, "min_days_before_arrival": 0,
        "checkin_link_days": 7, "rank_vip_first": True, "match_profile": True,
        "price_guard": True, "price_guard_share": 0.4, "max_paid_offers": 2,
        "max_repeat_offers": 2, "personalize": True,
    }
    defaults.update(settings.agent_get(CONFIG_KEY, {}) or {})
    return defaults


# --------------------------------------------------------------------------
# skips
# --------------------------------------------------------------------------
def skip_reason(store: Store, res: Reservation) -> str | None:
    if (res.extra or {}).get("outreach_status") == "already_sent":
        return "Already emailed - never double-mail a guest"
    upgrade_item = store.get_by_external("upgrade", res.id)
    if upgrade_item is not None and upgrade_item.review_status == "sent" and \
            not (upgrade_item.payload or {}).get("response"):
        # The offer has actually reached the guest and nothing has come back
        # yet - a draft still waiting on a human's approval is not "open" in
        # this sense, so it does not block outreach on its own.
        return "Active upgrade conversation open - no cross-selling mid-thread"
    return None


# --------------------------------------------------------------------------
# templates - see docs/how-it-works.md step 8. Structure ported from
# specs/pre-arrival-outreach-ai.md section 7; wording is this repo's own.
# --------------------------------------------------------------------------
def subject_for(hotel_name: str, res: Reservation, days: int, personalize: bool) -> str:
    if not personalize:
        return f"Ahead of your stay at {hotel_name}"
    extra = res.extra or {}
    tier, occasion = extra.get("tier"), extra.get("occasion")
    profile = extra.get("profile") or {}
    first = res.guest.first_name or "Guest"
    history = extra.get("history") or []
    if tier == "vip" and history:
        return f"Welcome back, {first} - your room is ready"
    if occasion == "anniversary":
        return f"Your anniversary at {hotel_name}"
    if occasion == "honeymoon":
        return f"Your honeymoon at {hotel_name}"
    if occasion == "birthday":
        return "A birthday during your stay - nearly time"
    if tier == "returning":
        return f"Welcome back, {first}"
    if profile.get("dog"):
        return f"{first} - we're ready for your dog too"
    if profile.get("baby"):
        return "Everything ready before you arrive"
    return f"{days} day(s) to go - everything ready for your stay"


def opener_for(res: Reservation) -> str | None:
    extra = res.extra or {}
    tier, occasion = extra.get("tier"), extra.get("occasion")
    profile = extra.get("profile") or {}
    history = extra.get("history") or []
    if history and tier in ("vip", "returning"):
        return ("It is nearly time for another stay with us - the whole team is "
                "looking forward to having you back.")
    if occasion == "anniversary":
        return "Happy anniversary from all of us."
    if occasion == "honeymoon":
        return "Congratulations on the wedding - we've made a note of the honeymoon."
    if occasion == "birthday":
        return "We noticed a birthday falls during your stay - leave that evening to us."
    if profile.get("dog"):
        return profile["dog"]
    if profile.get("baby"):
        return profile["baby"]
    if profile.get("work"):
        return "The desk is by the window and there's good coffee - your room is set up to work from."
    return None


def body_for(hotel_name: str, res: Reservation, offers_chosen: list[dict], *,
            checkin_link: bool, personalize: bool, days: int) -> str:
    extra = res.extra or {}
    if not personalize:
        salutation = "Dear guest,"
    elif extra.get("family_name"):
        salutation = f"Dear {extra['family_name']} family,"
    else:
        salutation = f"Dear {res.guest.first_name or 'Guest'},"

    opener = opener_for(res) if personalize else None
    if not opener:
        opener = f"We are looking forward to welcoming you in {days} day(s)."

    lines = [salutation, "", opener, "", "A few things we can have ready for you:"]
    for row in offers_chosen:
        lines.append(f"- {row['offer'].title} - {price_label(row['offer'])}")
    lines += ["", "Reply to this email and we will arrange any of them - nothing "
                   "is booked until you say so."]
    if checkin_link:
        lines += ["", "Your digital check-in link will follow separately - two "
                       "minutes now and your keys are ready when you arrive."]
    lines += ["", "Warm regards,", f"The {hotel_name} team"]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# the scan
# --------------------------------------------------------------------------
def _payload(res: Reservation, days: int) -> dict:
    extra = res.extra or {}
    return {
        "reservation_id": res.id, "external_ref": res.external_ref or res.id,
        "guest_name": res.guest.full_name or "Guest", "guest_email": res.guest.email,
        "room_type": res.room_type_name,
        "check_in": res.check_in, "check_out": res.check_out,
        "nights": nights(res.check_in, res.check_out), "days_out": days,
        "channel": res.source, "tier": extra.get("tier"), "occasion": extra.get("occasion"),
    }


def scan(settings: Settings, store: Store, pms, *, today: date,
         limit: int | None = None, dry_run: bool = False) -> dict:
    """Draft one pre-arrival email per eligible reservation. See docs/how-it-works.md."""
    cfg = _cfg(settings)
    stats = {"enabled": bool(cfg["enabled"]), "in_window": 0, "drafted": 0,
             "skipped": 0, "skips": [], "guard_swaps": 0}
    if not cfg["enabled"]:
        return stats

    window = int(cfg["outreach_window_days"])
    date_from, date_to = iso(today), iso(today + timedelta(days=window))
    reservations = pms.list_reservations(date_from, date_to, status="confirmed")

    candidates: list[tuple[int, Reservation]] = []
    for res in reservations:
        d = days_out(today, res.check_in)
        if d < 0 or d < int(cfg["min_days_before_arrival"]) or d > window:
            continue
        candidates.append((d, res))
    stats["in_window"] = len(candidates)

    if cfg["rank_vip_first"]:
        candidates.sort(key=lambda t: -((t[1].extra or {}).get("vip_score") or 0))
    else:
        candidates.sort(key=lambda t: t[0])

    offers = load_offers(settings)
    for d, res in candidates:
        if limit and stats["drafted"] >= limit:
            break
        reason = skip_reason(store, res)
        if reason:
            stats["skipped"] += 1
            stats["skips"].append({"reservation": res.external_ref or res.id, "reason": reason})
            continue
        existing = store.get_by_external("outreach", res.id)
        # A row still in "new" is one this scan created and then never
        # finished (a crash between upsert_unique and transition) - retry
        # it, don't skip it forever. Matches core.store.already_processed().
        if existing is not None and existing.review_status != "new":
            reason = STATE_SKIP_REASON.get(
                existing.review_status, f"already tracked as '{existing.review_status}'")
            stats["skipped"] += 1
            stats["skips"].append({"reservation": res.external_ref or res.id, "reason": reason})
            continue
        if dry_run:
            # --dry-run computes everything and writes nothing at all, not
            # even the dedup row - see core/review.py's write guard.
            stats["drafted"] += 1
            continue

        item, _ = store.upsert_unique("outreach", res.id, payload=_payload(res, d))
        chosen, swaps = match_offers(
            res, offers, match_profile=bool(cfg["match_profile"]),
            price_guard=bool(cfg["price_guard"]),
            price_guard_share=float(cfg["price_guard_share"]),
            max_paid=int(cfg["max_paid_offers"]), max_repeat=int(cfg["max_repeat_offers"]))
        stats["guard_swaps"] += swaps
        checkin_link = d <= int(cfg["checkin_link_days"])
        subject = subject_for(settings.hotel.name, res, d, bool(cfg["personalize"]))
        body = body_for(settings.hotel.name, res, chosen, checkin_link=checkin_link,
                        personalize=bool(cfg["personalize"]), days=d)
        n = nights(res.check_in, res.check_out)
        draft = {
            "subject": subject, "body": body, "checkin_link": checkin_link,
            "offers": [{"id": row["offer"].id, "title": row["offer"].title,
                       "price": row["offer"].price, "value": offer_value(row["offer"], n),
                       "because": row["because"]} for row in chosen],
        }
        store.set_fields(item.id, draft=draft, intent="outreach")
        store.transition(item.id, "pending_review", actor="agent",
                         detail={"days_out": d, "offers": len(chosen)})
        log.info("queued", item_id=item.id, kind="outreach",
                external_ref=res.external_ref or res.id, days_out=d, offers=len(chosen))
        stats["drafted"] += 1
    return stats


# --------------------------------------------------------------------------
# guest response - a human reads the reply and records it. Not a guarded
# write: this only records what the guest said, in our own database.
# --------------------------------------------------------------------------
def respond(store: Store, item_id: str, accept_ids: list[str] | None = None,
           note: str = "") -> Item:
    item = store.get_item(item_id)
    if item is None:
        raise KeyError(f"no item {item_id}")
    accept_ids = accept_ids or []
    offers = (item.draft or {}).get("offers") or []
    chosen = [o for o in offers if o["id"] in accept_ids]
    response = {
        "outcome": "accepted" if chosen else "declined",
        "accepted_offers": [o["id"] for o in chosen],
        "accepted_value": sum(o.get("value", o.get("price", 0)) for o in chosen),
        "note": note,
    }
    payload = dict(item.payload or {})
    payload["response"] = response
    store.set_fields(item.id, payload=payload)
    store.record_event(item.id, "human", "guest_response", response)
    log.info("guest response recorded", item_id=item.id, kind="outreach",
             outcome=response["outcome"], accepted_value=response["accepted_value"])
    updated = store.get_item(item.id)
    assert updated is not None
    return updated


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def cmd_scan(store: Store, settings: Settings, args) -> int:
    pms = get_pms(settings)
    today = date.fromisoformat(args.as_of) if args.as_of else date.today()
    stats = scan(settings, store, pms, today=today, limit=args.limit, dry_run=args.dry_run)
    print(f"Track A (outreach): {stats['drafted']} drafted, {stats['skipped']} skipped, "
         f"{stats['in_window']} in window, {stats['guard_swaps']} offer(s) swapped by "
         f"the price guard.")
    for skip in stats["skips"][:20]:
        print(f"  skipped {skip['reservation']}: {skip['reason']}")
    return 0


def cmd_respond(store: Store, args) -> int:
    accept = [x.strip() for x in (args.accept or "").split(",") if x.strip()]
    try:
        item = respond(store, args.id, accept_ids=accept, note=args.note or "")
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    response = (item.payload or {}).get("response", {})
    print(f"recorded {response.get('outcome')} for {item.id} "
         f"(value EUR {response.get('accepted_value', 0):.0f})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="draft outreach emails for eligible arrivals")
    p_scan.add_argument("--as-of", default=None, help="override today, YYYY-MM-DD")
    p_scan.add_argument("--limit", type=int, default=None)
    p_scan.add_argument("--dry-run", action="store_true")

    p_respond = sub.add_parser("respond", help="record what the guest replied")
    p_respond.add_argument("id")
    p_respond.add_argument("--accept", default="", help="comma-separated offer ids the guest wants")
    p_respond.add_argument("--note", default="")

    args = parser.parse_args(argv)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    try:
        if args.command == "scan":
            return cmd_scan(store, settings, args)
        if args.command == "respond":
            return cmd_respond(store, args)
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

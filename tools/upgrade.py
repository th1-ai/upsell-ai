#!/usr/bin/env python3
"""tools/upgrade.py - Track B, "The Closer": paid room upgrades.

    python3 tools/upgrade.py scan [--as-of YYYY-MM-DD] [--limit N] [--dry-run]
    python3 tools/upgrade.py respond <offer-id> --outcome accept|decline [--note "..."]
    python3 tools/upgrade.py execute [--limit N]
    python3 tools/upgrade.py sweep-expired [--hours 72]

Deliberately LLM-free - see docs/how-it-works.md. `scan()` and `sweep_expired()`
are called by tools/run.py; `respond` and `execute` are run by a human once a
guest has actually replied to an offer.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import get_email, get_pms  # noqa: E402
from core.adapters.base import AdapterError, Reservation  # noqa: E402
from core.config import ConfigError, Settings, load_settings  # noqa: E402
from core.log import get_logger  # noqa: E402
from core.review import WriteBlocked  # noqa: E402
from core.store import Item, Store, StoreError  # noqa: E402
from tools.domain import days_out, iso, money, nights, nights_stayed, round_to_5  # noqa: E402

CONFIG_KEY = "subagents.room_upgrade"
OPEN_STATES = ("pending_review", "needs_human", "approved", "edited", "sending")
log = get_logger("upgrade", quiet=True)

#: fixed reasons per technical send-status - see docs/how-it-works.md step 5.
#: A guest's actual answer (accepted / declined) lives in the item's payload,
#: not the FSM - "sent" is terminal in core.store, so a reply that arrives
#: after the email went out is recorded as data, not a status transition.
#:
#: MUST cover every core.store.STATUSES value except "new" (a row still "new"
#: is one this scan created and then never finished - a crash between
#: upsert_unique and transition - and is retried, not skipped forever; see
#: _skip() below and tools/outreach.py's identical comment). `.get()` falls
#: back to a generic-but-readable reason for anything not listed here, so an
#: unrecognised status is always skipped with a reason - never treated as
#: brand new and re-drafted, which is what crashed the scan in SIMULATION.md
#: round 2 finding 1 (a shadow-blocked send left an item "failed" with no
#: entry here, so the next scan tried `pending_review` on it and the FSM
#: correctly refused).
STATE_SKIP_REASON = {
    "dispatched": "already queued for a decision - one ask per stay",
    "pending_review": "offer already out - one ask per stay",
    "needs_human": "offer already out, held for a human OK - one ask per stay",
    "approved": "offer approved and about to send - one ask per stay",
    "edited": "offer approved and about to send - one ask per stay",
    "sending": "offer is sending right now - one ask per stay",
    "sent": "offer already sent - waiting on the guest's reply",
    "stale": "let the offer lapse - not asked twice",
    "failed": "previous offer failed to send - run `python3 tools/review.py retry <id>` "
             "before this will try again",
    "rejected": "offer was rejected - not re-drafting automatically",
    "auto_sent": "already auto-sent",
    "skipped": "already marked skipped",
}


def _cfg(settings: Settings) -> dict:
    defaults = {
        "enabled": True,
        "room_ladder": ["Classic Room", "Deluxe Sea View", "Junior Suite", "Meridian Suite"],
        "window_days": 14, "upgrade_factor": 0.55,
        "surcharge_band": {"min": 10, "max": 100},
        "group_exclude": True, "offer_valid_hours": 72, "auto_confirm": False,
    }
    defaults.update(settings.agent_get(CONFIG_KEY, {}) or {})
    return defaults


# --------------------------------------------------------------------------
# pricing
# --------------------------------------------------------------------------
def rack_rate(pms, room_type: str, dates: list[str], fallback: float) -> float:
    """Mean rack price for ``room_type`` across ``dates``. Falls back if unpriced."""
    if not dates:
        return fallback
    rows = pms.get_rates(dates[0], dates[-1], room_type=room_type)
    prices = [r.price for r in rows if r.date in dates and r.price]
    return (sum(prices) / len(prices)) if prices else fallback


def availability_ok(pms, room_type: str, dates: list[str]) -> bool:
    if not dates:
        return False
    rows = {r.date: r for r in pms.get_rates(dates[0], dates[-1], room_type=room_type)}
    return all(d in rows and rows[d].available > 0 for d in dates)


def surcharge_for(rate_from: float, rate_to: float, factor: float) -> int:
    return round_to_5((rate_to - rate_from) * factor)


def band_flag(per_night: int, band: dict) -> str | None:
    if per_night > int(band.get("max", 100)):
        return "over_band"
    if per_night < int(band.get("min", 10)):
        return "under_band"
    return None


# --------------------------------------------------------------------------
# templates - structure ported from specs/room-upgrade-ai.md section 7;
# wording is this repo's own.
# --------------------------------------------------------------------------
def _occasion_line(occasion: str | None) -> str | None:
    if occasion == "anniversary":
        return "We can see this stay marks an anniversary - congratulations from all of us here."
    if occasion == "honeymoon":
        return "Congratulations on the wedding - a room with a view seems like the right idea."
    if occasion == "birthday":
        return "A birthday during your stay deserves a little more than a room key."
    return None


def offer_email(res: Reservation, to_room: str, per_night: int, total: int, n: int,
                currency: str) -> tuple[str, str]:
    extra = res.extra or {}
    occasion = extra.get("occasion")
    subject = (f"A little {occasion} thought - the {to_room.lower()}" if occasion
              else f"One idea before you arrive - the {to_room.lower()}")
    lines = []
    occ = _occasion_line(occasion)
    if occ:
        lines += [occ, ""]
    lines += [
        f"Your {res.room_type_name} is booked and waiting. We're holding a "
        f"{to_room} for your dates, and rather than let it sit, we'd offer it to "
        f"you first: {money(per_night, currency)} a night more "
        f"({money(total, currency)} across your {n} night(s)) - about half what "
        "it would cost to rebook.",
        "",
        "No pressure at all - reply yes and it's done: card on file, room "
        f"changed, nothing else to do. Everything else about {res.external_ref or res.id} "
        "stays exactly as it is.",
    ]
    return subject, "\n".join(lines)


def confirmation_email(external_ref: str, to_room: str, total: int, new_total: float,
                       currency: str) -> tuple[str, str]:
    subject = f"Confirmed - you're in the {to_room}"
    body = (f"Done - {external_ref} is now a {to_room}. We've charged "
           f"{money(total, currency)} to the card on file and your new stay total "
           f"is {money(new_total, currency)}. Dates, breakfast and everything else "
           "stay exactly as they were.\n\nWe'll have the room ready. See you soon.")
    return subject, body


def pms_note(from_room: str, to_room: str, per_night: int, n: int, total: int,
            currency: str, today: date) -> str:
    return (f"[AI {today.isoformat()}] Upgrade accepted: {from_room} -> {to_room}, "
           f"+{money(per_night, currency)}/night x {n} = +{money(total, currency)} "
           f"charged to card on file. Rate adjusted; {from_room} released back to inventory.")


# --------------------------------------------------------------------------
# the scan
# --------------------------------------------------------------------------
def _skip(store: Store, res: Reservation) -> str | None:
    existing = store.get_by_external("upgrade", res.id)
    if existing is None:
        return None
    if existing.review_status == "new":
        # Crashed mid-creation before the first transition - retry it, don't
        # skip it forever. Matches core.store.already_processed() and
        # tools/outreach.py's identical "new" handling.
        return None
    response = (existing.payload or {}).get("response")
    if response:
        if response.get("outcome") == "declined":
            return "declined the offer - a no is final"
        if response.get("outcome") == "accepted":
            return "already upgraded - done"
    # Never return None past this point for an existing item: an unmapped
    # status must still be skipped with a readable reason, never fall
    # through and be treated as brand new (that is what crashed the scan in
    # SIMULATION.md round 2 finding 1).
    return STATE_SKIP_REASON.get(
        existing.review_status, f"already tracked as '{existing.review_status}'")


def _payload(res: Reservation, from_room: str, to_room: str, per_night: int,
            total_delta: int, n: int, days: int) -> dict:
    return {
        "reservation_id": res.id, "external_ref": res.external_ref or res.id,
        "guest_name": res.guest.full_name or "Guest", "guest_email": res.guest.email,
        "from_room": from_room, "to_room": to_room, "per_night": per_night, "nights": n,
        "total_delta": total_delta, "days_out": days, "total": res.total,
        "channel": res.source, "occasion": (res.extra or {}).get("occasion"),
    }


def scan(settings: Settings, store: Store, pms, *, today: date,
        limit: int | None = None, dry_run: bool = False) -> dict:
    """Draft one upgrade offer per eligible reservation. See docs/how-it-works.md."""
    cfg = _cfg(settings)
    stats = {"enabled": bool(cfg["enabled"]), "candidates": 0, "sent": 0, "held": 0,
             "skipped": 0, "skips": []}
    if not cfg["enabled"]:
        return stats

    ladder = list(cfg["room_ladder"])
    window = int(cfg["window_days"])
    factor = float(cfg["upgrade_factor"])
    band = dict(cfg["surcharge_band"])
    currency = settings.hotel.currency
    date_from, date_to = iso(today), iso(today + timedelta(days=window + 30))
    reservations = pms.list_reservations(date_from, date_to, status="confirmed")

    def note(res, reason):
        stats["skipped"] += 1
        stats["skips"].append({"reservation": res.external_ref or res.id, "reason": reason})

    for res in reservations:
        d = days_out(today, res.check_in)
        if d < 0:
            note(res, "already in house - in-stay upsells belong to a person")
            continue
        if bool(cfg["group_exclude"]) and str(res.source or "").lower() == "group":
            note(res, "group block - a person owns this conversation")
            continue
        if res.room_type_name not in ladder:
            note(res, f"'{res.room_type_name}' is not on the configured room ladder")
            continue
        idx = ladder.index(res.room_type_name)
        if idx >= len(ladder) - 1:
            note(res, f"already in the {res.room_type_name} - top of the ladder")
            continue
        reason = _skip(store, res)
        if reason:
            note(res, reason)
            continue
        if d > window:
            note(res, f"outside the {window}-day window - enters it in {d - window} day(s)")
            continue
        stats["candidates"] += 1
        if limit and (stats["sent"] + stats["held"]) >= limit:
            continue

        from_room, to_room = res.room_type_name, ladder[idx + 1]
        stay_dates = nights_stayed(res.check_in, res.check_out)
        if not availability_ok(pms, to_room, stay_dates):
            note(res, f"no {to_room} free across the stay - nothing to sell tonight")
            continue

        n = len(stay_dates)
        rate_from = (res.total or 0.0) / n if n else 0.0
        rate_to = rack_rate(pms, to_room, stay_dates, fallback=rate_from * 1.3)
        per_night = surcharge_for(rate_from, rate_to, factor)
        total_delta = per_night * n
        flag = band_flag(per_night, band)

        if dry_run:
            # --dry-run computes the price and the band decision and writes
            # nothing at all, not even the dedup row.
            stats["held" if flag else "sent"] += 1
            continue

        item, _ = store.upsert_unique(
            "upgrade", res.id,
            payload=_payload(res, from_room, to_room, per_night, total_delta, n, d))
        subject, body = offer_email(res, to_room, per_night, total_delta, n, currency)
        store.set_fields(item.id, draft={"subject": subject, "body": body,
                                         "from_room": from_room, "to_room": to_room,
                                         "per_night": per_night, "total_delta": total_delta,
                                         "nights": n},
                         intent="upgrade")
        if flag:
            store.transition(item.id, "needs_human", actor="agent",
                             detail={"flag": flag, "per_night": per_night, "band": band})
            log.info("queued", item_id=item.id, kind="upgrade", status="needs_human",
                    external_ref=res.external_ref or res.id, flag=flag, per_night=per_night)
            stats["held"] += 1
        else:
            store.transition(item.id, "pending_review", actor="agent",
                             detail={"per_night": per_night})
            log.info("queued", item_id=item.id, kind="upgrade", status="pending_review",
                    external_ref=res.external_ref or res.id, per_night=per_night)
            stats["sent"] += 1
    return stats


# --------------------------------------------------------------------------
# guest response - accept spawns a second, separately-approved item for the
# actual PMS change; decline is recorded as data, never a counter-offer.
# See docs/how-it-works.md "Track B" and "Design decisions" #6.
# --------------------------------------------------------------------------
def respond(store: Store, item_id: str, outcome: str, note: str = "") -> Item:
    if outcome not in ("accept", "decline"):
        raise ValueError("outcome must be 'accept' or 'decline'")
    item = store.get_item(item_id)
    if item is None:
        raise KeyError(f"no item {item_id}")
    payload = dict(item.payload or {})
    payload["response"] = {"outcome": "accepted" if outcome == "accept" else "declined",
                           "note": note}
    store.set_fields(item_id, payload=payload)
    store.record_event(item_id, "human",
                       "guest_accepted" if outcome == "accept" else "guest_declined",
                       {"note": note})
    log.info("guest response recorded", item_id=item_id, kind="upgrade", outcome=outcome)
    if outcome == "accept":
        exec_item, created = store.upsert_unique(
            "upgrade_execute", payload.get("reservation_id", item_id), payload=payload)
        # Retry a row stuck in "new" (a crash between the two lines below on
        # an earlier call) the same as a freshly created one - never skip it
        # forever. Matches core.store.already_processed().
        if created or exec_item.review_status == "new":
            store.set_fields(exec_item.id, intent="upgrade_execute", draft={
                "subject": f"Execute upgrade for {payload.get('external_ref')}",
                "from_room": payload.get("from_room"), "to_room": payload.get("to_room"),
                "per_night": payload.get("per_night"), "total_delta": payload.get("total_delta"),
                "nights": payload.get("nights"),
            })
            store.transition(exec_item.id, "pending_review", actor="agent",
                             detail={"source_offer": item_id})
            log.info("queued", item_id=exec_item.id, kind="upgrade_execute",
                    status="pending_review", source_offer=item_id)
        payload["execute_item_id"] = exec_item.id
        store.set_fields(item_id, payload=payload)
    return store.get_item(item_id)


def execute_one(settings: Settings, store: Store, pms, email, item: Item, today: date) -> Item:
    """Perform the guarded PMS writes + confirmation email for one accepted upgrade.

    ``item`` must already be ``sending`` (the caller claimed it) so the review
    guard sees the approval - see tools/review.py's kind-aware ``send``.
    """
    payload = item.payload or {}
    currency = settings.hotel.currency
    reservation_id = payload.get("reservation_id", "")
    to_room, from_room = payload.get("to_room", ""), payload.get("from_room", "")
    per_night, total_delta = int(payload.get("per_night", 0)), int(payload.get("total_delta", 0))
    n = int(payload.get("nights", 1))
    res = pms.get_reservation(reservation_id)
    current_total = res.total if res is not None else float(payload.get("total", 0) or 0)
    new_total = current_total + total_delta

    pms.update_reservation(reservation_id,
                           {"room_type_name": to_room, "total": new_total}, item=item)
    pms.add_note(reservation_id,
                pms_note(from_room, to_room, per_night, n, total_delta, currency, today),
                item=item)
    subject, body = confirmation_email(payload.get("external_ref", reservation_id), to_room,
                                       total_delta, new_total, currency)
    result = email.send(payload.get("guest_email", ""), subject, body, item=item)
    updated = store.mark_sent(item.id, result.get("message_id"))
    log.info("upgrade executed", item_id=item.id, kind="upgrade_execute",
            reservation_id=reservation_id, from_room=from_room, to_room=to_room,
            total_delta=total_delta)
    return updated


def sweep_expired(store: Store, hours: int | None = None) -> list[str]:
    """Offers nobody answered within the validity window quietly lapse.

    Reuses core.store's own ``mark_stale`` (pending_review/needs_human items
    untouched past the cutoff) rather than a second, parallel expiry idea.
    """
    return store.mark_stale(older_than_hours=hours or 72)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def cmd_scan(store: Store, settings: Settings, args) -> int:
    pms = get_pms(settings)
    today = date.fromisoformat(args.as_of) if args.as_of else date.today()
    stats = scan(settings, store, pms, today=today, limit=args.limit, dry_run=args.dry_run)
    print(f"Track B (upgrade): {stats['sent']} offer(s) drafted and ready for approval, "
         f"{stats['held']} held (outside the surcharge band), {stats['skipped']} skipped, "
         f"{stats['candidates']} candidate(s) seen.")
    for skip in stats["skips"][:20]:
        print(f"  skipped {skip['reservation']}: {skip['reason']}")
    return 0


def cmd_respond(store: Store, args) -> int:
    try:
        item = respond(store, args.id, args.outcome, note=args.note or "")
    except (KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    response = (item.payload or {}).get("response", {})
    line = f"recorded {response.get('outcome')} for {item.id}"
    if args.outcome == "accept":
        line += f" - execute item {item.payload.get('execute_item_id')} queued for approval"
    print(line)
    return 0


def cmd_execute(store: Store, settings: Settings, args) -> int:
    pms, email = get_pms(settings), get_email(settings)
    today = date.today()
    items = store.list_items(status=["approved", "edited"], kind="upgrade_execute",
                             limit=args.limit)
    if not items:
        print("Nothing approved is waiting to execute.")
        return 0
    done, failed = 0, 0
    for item in items:
        claimed = store.transition(item.id, "sending", actor="agent", detail={"claim": True})
        try:
            execute_one(settings, store, pms, email, claimed, today)
        except WriteBlocked as exc:
            store.mark_send_failed(item.id, str(exc))
            print(f"blocked {item.id}: {exc}")
            failed += 1
            continue
        except Exception as exc:  # noqa: BLE001 - record and move on, never crash the batch
            store.mark_send_failed(item.id, str(exc))
            print(f"failed {item.id}: {exc}")
            failed += 1
            continue
        print(f"executed {item.id}")
        done += 1
    print(f"\n{done} executed, {failed} failed.")
    return 0 if failed == 0 else 1


def cmd_sweep(store: Store, args) -> int:
    stale = sweep_expired(store, hours=args.hours)
    print(f"{len(stale)} offer(s) lapsed and went stale.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="draft upgrade offers for eligible arrivals")
    p_scan.add_argument("--as-of", default=None, help="override today, YYYY-MM-DD")
    p_scan.add_argument("--limit", type=int, default=None)
    p_scan.add_argument("--dry-run", action="store_true")

    p_respond = sub.add_parser("respond", help="record the guest's answer")
    p_respond.add_argument("id")
    p_respond.add_argument("--outcome", choices=["accept", "decline"], required=True)
    p_respond.add_argument("--note", default="")

    p_execute = sub.add_parser("execute", help="perform the PMS write for approved upgrades")
    p_execute.add_argument("--limit", type=int, default=20)

    p_sweep = sub.add_parser("sweep-expired", help="mark unanswered offers stale")
    p_sweep.add_argument("--hours", type=int, default=None)

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
        if args.command == "execute":
            return cmd_execute(store, settings, args)
        if args.command == "sweep-expired":
            return cmd_sweep(store, args)
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

#!/usr/bin/env python3
"""tools/demo.py - the whole loop on the bundled fixtures, zero credentials.

    make demo
    python3 tools/demo.py

Uses `load_settings(demo=True)` (core/config.py), which forces `llm.provider:
mock`, `mode: shadow`, and every `systems.*.adapter` to `mock`, regardless of
whatever `config/hotel.yaml` currently says - so this always works on a fresh
clone with a blank .env, and never silently starts reading a real mailbox or
PMS once you've connected one (ARCHITECTURE.md section 1, "works in 5 minutes
with zero credentials"). It runs "as of" the fixtures' anchor date
(2026-09-01, see fixtures/hotel/property.md) rather than the real today, so
the output is the same every time however far in the future you run this. It
runs against its own database (data/demo/demo.db) and never touches
data/agent.db (that is `make run`'s file).

Prints one line every check reads for the pass/fail signal:

    DEMO OK - N outreach drafted, N upgrade offer(s) drafted, N held (shadow)

Neither scan ever sends anything, and this file never calls `email.send()`.
The one thing standing in for real activity is `_seed_prior_offer_sent()`
below: it writes HA-1105's upgrade offer straight to the database as
`sent`, the same row shape a real send would leave behind, so Track A's
cross-link skip (docs/how-it-works.md "Design decisions" #3) has something
real to skip. That is data pretending to be "this conversation already
happened before you started using this agent," not an outbound action - see
`workflows/80-review.md` for what an actual approve + send looks like, by
hand, in shadow and in live mode.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import get_pms  # noqa: E402
from core.config import ConfigError, load_settings, sub_data_dir  # noqa: E402
from core.store import Store  # noqa: E402
from tools import coach, outreach, upgrade  # noqa: E402
from tools.run import narrate  # noqa: E402

#: the one upgrade offer this demo pre-seeds as already sent, purely so
#: Track A's cross-link skip (docs/how-it-works.md "Design decisions" #3) has
#: something real to skip - everything else stays untouched in shadow mode,
#: and nothing here ever calls an adapter's send().
DEMO_SEED_RESERVATION = "res-105"

DEMO_AS_OF = date(2026, 9, 1)  # matches fixtures/hotel/property.md's anchor date


def _seed_prior_offer_sent(store) -> str | None:
    """Move `DEMO_SEED_RESERVATION`'s upgrade offer straight to `sent`.

    This writes to `core.store` directly (`transition` / `mark_sent`) and
    never touches an adapter - `email.send()` is never called, so shadow
    mode's "nothing leaves the building" guarantee holds for the whole demo,
    including this row. It stands in for a stay whose offer already went out
    before you started running this agent, exactly the shape a real send
    would leave in the database, so Track A's cross-link skip below has a
    real "sent" conversation to point at instead of an invented one.
    """
    item = store.get_by_external("upgrade", DEMO_SEED_RESERVATION)
    if item is None or item.review_status != "pending_review":
        return None
    store.transition(item.id, "approved", actor="agent",
                     detail={"note": "demo seed: pretend this offer already went out"})
    store.transition(item.id, "sending", actor="agent", detail={"claim": True})
    store.mark_sent(item.id, message_id="demo-seed")
    return (item.payload or {}).get("external_ref")


def main() -> int:
    try:
        settings = load_settings(demo=True)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    demo_db = sub_data_dir("demo") / "demo.db"
    if demo_db.exists():
        demo_db.unlink()  # every `make demo` is a clean, repeatable run
    store = Store(settings, path=demo_db)
    store.migrate(coach.SCHEMA)
    pms = get_pms(settings)

    print(f"Upsell AI demo - arrivals book as of {DEMO_AS_OF.isoformat()} "
         f"from fixtures/hotel/reservations.json\n")

    # Track B runs first: an outreach candidate with a fresh, open upgrade
    # offer must be skipped for real (docs/how-it-works.md "Design decisions"
    # #3), which only happens if the upgrade item already exists when Track A
    # looks for it.
    u_stats = upgrade.scan(settings, store, pms, today=DEMO_AS_OF)
    print(f"Track B - Room Upgrade: {u_stats['sent']} offer(s) drafted and ready for "
         f"approval, {u_stats['held']} held for a human OK (outside the surcharge "
         f"band), {u_stats['skipped']} skipped.")
    for skip in u_stats["skips"]:
        print(f"    skipped {skip['reservation']}: {skip['reason']}")

    seeded_ref = _seed_prior_offer_sent(store)
    if seeded_ref:
        print(f"\n(For this demo only, {seeded_ref}'s upgrade offer is pre-seeded as "
             "already sent - written straight to the database, never through "
             "email.send() - so Track A below has a real conversation to skip. "
             "See docs/how-it-works.md.)")

    o_stats = outreach.scan(settings, store, pms, today=DEMO_AS_OF)
    print(f"\nTrack A - Pre-Arrival Outreach: {o_stats['drafted']} drafted, "
         f"{o_stats['skipped']} skipped, {o_stats['in_window']} in the "
         f"{settings.agent_get('subagents.pre_arrival_outreach.outreach_window_days', 21)}"
         "-day window.")
    for skip in o_stats["skips"]:
        print(f"    skipped {skip['reservation']}: {skip['reason']}")

    stats = {"outreach": o_stats, "upgrade": u_stats}
    note = narrate(settings, store, stats, provider="mock")
    print(f"\n{note}")

    print("\nEverything else stayed in shadow mode: neither scan ever calls send() on "
         "its own, and every other draft above is still just sitting in the queue.")
    print("Next: `make review` to see the drafts, or read workflows/10-upsell-journey.md.\n")

    print(f"DEMO OK - {o_stats['drafted']} outreach drafted, {u_stats['sent']} upgrade "
         f"offer(s) drafted, {u_stats['held']} held ({settings.mode})")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

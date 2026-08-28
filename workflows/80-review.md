# Workflow: working the review queue

Objective: turn a queued draft into a decision - approve, edit, or reject -
and, once approved, actually send it.

Nothing reaches a guest, and nothing is written to your PMS, without going
through this. `mode: shadow` blocks `send_email` and `pms_write` for
everything, including an item you have approved or edited - shadow is a
global kill switch, not a per-item exception; see `docs/safety.md` for the
full guard.

## Steps

1. **See what is waiting.**
   ```bash
   make review
   python3 tools/review.py list --kind outreach
   python3 tools/review.py list --kind upgrade
   python3 tools/review.py list --kind upgrade_execute
   ```
   Each line shows the item id, status, kind, guest, and (for upgrade items)
   the room move.

2. **Read one in full.**
   ```bash
   python3 tools/review.py show <id>
   ```
   For an outreach item this is the arrival details, the drafted email, the
   `because` trace on every offer, and the full event history. For an
   upgrade item it's the from/to room, the surcharge, and - if it's
   `needs_human` - the band flag that held it. For an `upgrade_execute` item
   it's the exact PMS patch that will run. Summarize it for the hotel in
   plain language - do not paste the raw JSON at them.

3. **Decide.**
   ```bash
   python3 tools/review.py approve <id>
   python3 tools/review.py edit <id> --body-file my-version.txt [--subject "New subject"]
   python3 tools/review.py reject <id> --reason "wrong tone"
   ```
   `edit` records the before/after pair as a `learnings` row - that is what
   the weekly coach clusters into suggestions (`workflows/85-coach-weekly.md`).

4. **Send what was approved.**
   ```bash
   python3 tools/review.py send                    # everything approved/edited, any kind
   python3 tools/review.py send --kind outreach     # just the outreach queue
   python3 tools/review.py send --kind upgrade      # just the upgrade offers
   ```
   `send` dispatches by `kind`: `outreach` and `upgrade` items go out as a
   plain email; `upgrade_execute` items instead run the guarded PMS write
   (room type, total, note) and then send the confirmation - see
   `docs/how-it-works.md`. In `mode: shadow` every one of these is blocked,
   even an item you just approved - `send` reports it as `blocked` and
   **keeps the approval**: the item goes right back to `approved` (never
   `failed`), so it stays queued and is picked up the moment you switch to
   `mode: live` - no `retry` needed. Nothing sends while shadow is on,
   full stop.

5. **A failed send.** `failed` means a real send failure - the mailbox
   rejected the message, a PMS write the adapter bounced, the item's own
   error attached - never a shadow block (step 4 keeps a shadow-blocked item
   `approved`, not `failed`).
   ```bash
   python3 tools/review.py retry <id>
   ```
   re-queues it for another attempt once you've fixed the cause (usually a
   mailbox credential, or a PMS write that the adapter rejected) -
   `make doctor` says which.

## Rules

- Only `tools/review.py` writes `approved` / `edited` / `rejected` / `stale`.
- `python3 tools/review.py stale` is the go-live step that clears the
  shadow-era backlog (`workflows/90-go-live.md`) - everything still
  un-sent moves to `stale` so nothing old goes out by surprise the moment
  you flip to live.
- A guest's actual answer - accepted, declined - is recorded separately by
  `tools/outreach.py respond` / `tools/upgrade.py respond`, not through this
  queue; see the two `2x-*` workflows.
- Confirm with the hotel before sending anything, even an approved item, the
  first few times. `workflows/90-go-live.md` covers when to stop doing that.

# Workflow: Room Upgrade AI ("The Closer")

Objective: run Track B on its own, record a guest's answer, and execute an
accepted upgrade.

## Inputs

- `subagents.room_upgrade` in `config/agent.yaml` - `room_ladder` (must match
  your PMS's room type names exactly), `window_days`, `upgrade_factor`,
  `surcharge_band`.

## Steps

1. **Scan.**
   ```bash
   python3 tools/upgrade.py scan
   python3 tools/upgrade.py scan --as-of 2026-09-15
   python3 tools/upgrade.py scan --dry-run
   ```
   Prints offers drafted and ready for approval, offers held (outside the
   surcharge band), and every skip with its reason.

2. **A held draft needs a real decision, not a rubber stamp.** Read it:
   ```bash
   python3 tools/review.py show <id>
   ```
   The `flag` in its event history says `over_band` or `under_band` and
   names the actual per-night figure against your configured band. Approve
   only if you're comfortable with that specific number for that specific
   guest - `docs/safety.md` covers why this one is never auto-approved.

3. **Approve and send the offer.**
   ```bash
   python3 tools/review.py approve <id>
   python3 tools/review.py send --kind upgrade
   ```

4. **The guest replies.**
   ```bash
   python3 tools/upgrade.py respond <id> --outcome decline --note "staying as booked"
   python3 tools/upgrade.py respond <id> --outcome accept
   ```
   `accept` creates a **second** item, `kind="upgrade_execute"` - the actual
   PMS write and the surcharge are a separate approval from sending the
   offer (`docs/how-it-works.md`, "Design decisions" #6).

5. **Approve and execute.**
   ```bash
   python3 tools/review.py list --kind upgrade_execute
   python3 tools/review.py approve <execute-id>
   python3 tools/upgrade.py execute
   ```
   This is the one moment this agent writes to your PMS: room type, total,
   and the note, all in one guarded pass, then the confirmation email.

6. **Sweep for silence.** Run this alongside the daily scan (`tools/run.py`
   already does):
   ```bash
   python3 tools/upgrade.py sweep-expired
   ```
   Marks anything unanswered past `offer_valid_hours` (default 72) `stale` -
   "the offer lapsed quietly and the room went back on sale."

## Edge cases

- **The target room has no availability for even one night of the stay.**
  Skipped outright, not offered at a discount or a shorter window -
  `docs/how-it-works.md` step 7.
- **A guest already declined once.** Never offered again, on any future
  scan, for this reservation - `docs/safety.md`.
- **`auto_confirm: true` in config.** Read `docs/how-it-works.md`,
  "Design decisions" #9 before relying on it: `execute` still goes through
  the review guard, so `auto_confirm` alone does not skip the human approval
  unless `pms_write` is also removed from `review.require_approval_for`.

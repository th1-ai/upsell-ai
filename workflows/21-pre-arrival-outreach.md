# Workflow: Pre-Arrival Outreach AI ("The Welcomer")

Objective: run Track A on its own, read a guest's reply, and record it.

## Inputs

- `subagents.pre_arrival_outreach` in `config/agent.yaml` - window, ranking,
  matching and price-guard knobs (see `docs/sub-agents.md`).
- `offers:` in `config/agent.yaml` - the catalogue Track A sells from.

## Steps

1. **Scan.**
   ```bash
   python3 tools/outreach.py scan
   python3 tools/outreach.py scan --as-of 2026-09-15   # preview a future date
   python3 tools/outreach.py scan --dry-run            # numbers only, writes nothing
   ```
   Prints how many were drafted, how many skipped and why, and how many
   premium offers the price guard swapped down to something cheaper.

2. **Read one draft in full.**
   ```bash
   python3 tools/review.py list --kind outreach
   python3 tools/review.py show <id>
   ```
   Check the `offers` array's `because` field for each pick - every one
   should trace to a real fact (an occasion, a profile flag, a past stay).
   If a `because` reads generic ("nothing invented"), that guest's profile is
   genuinely empty - see `docs/integrations.md` for wiring real guest data
   into `Reservation.extra`.

3. **Approve, edit or reject**, same as any item -
   `workflows/80-review.md`.

4. **A guest replies.** Read the reply in your mailbox, work out which
   offers (if any) they want, then:
   ```bash
   python3 tools/outreach.py respond <id> --accept of-chefs-table,of-cabana
   python3 tools/outreach.py respond <id>                     # no ids = declined everything
   python3 tools/outreach.py respond <id> --note "wants the chef's table only"
   ```
   This only records data in this repo's own database - it is not a guarded
   write, so it works regardless of `mode`. `make report` then counts the
   accepted value.

## Edge cases

- **A guest already has an open, unanswered upgrade offer.** Skipped, with
  the reason logged - see `docs/how-it-works.md` step 4. Nothing to do; it
  clears on its own once the guest answers the upgrade offer or it lapses.
- **`outreach_status: already_sent` on a reservation.** Set this on a
  reservation (via your PMS's custom field, mapped into `Reservation.extra`
  - see `docs/integrations.md`) when a guest was already reached through
  another channel, and Track A leaves them alone.
- **Re-running the scan for the same day.** Safe - dedup is per reservation,
  forever, not per day (`docs/how-it-works.md`, "Idempotency").

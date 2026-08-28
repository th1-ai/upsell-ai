# Workflow: the daily upsell run

Objective: run both tracks over the arrivals book and see what Upsell AI
drafted. This is the overview - `workflows/21-pre-arrival-outreach.md` and
`workflows/22-room-upgrades.md` go deeper on each track.

## Inputs

- A configured `systems.pms.adapter` (`mock` by default - see
  `workflows/00-setup.md` step 6 to connect a real one).
- `config/agent.yaml`'s `subagents.pre_arrival_outreach` and
  `subagents.room_upgrade` blocks - the defaults work; tune them once you've
  watched a few real passes.

## Steps

1. **Run one pass.**
   ```bash
   make run
   make run ARGS="--only outreach"     # just Track A
   make run ARGS="--only upgrade"      # just Track B
   make run ARGS="--dry-run"           # compute everything, write nothing
   make run ARGS="--limit 5"           # cap each track at 5 items
   ```
   Track B runs first, then Track A - deliberately, so a fresh upgrade offer
   can be seen as "open" by Track A's cross-link check on the same pass (see
   `docs/how-it-works.md`, "Design decisions" #3).

2. **If `llm.provider` is `interactive`,** the run stops with exit code 3 and
   parks the cosmetic run-summary prompt in `data/pending/`. This is the only
   place a run can pause - neither scan itself calls an LLM. Read the prompt,
   write your answer, run the same command again. Skipping it is also fine:
   the summary line is never required (docs/how-it-works.md).

3. **See what happened.**
   ```bash
   make review
   python3 tools/review.py list --kind outreach
   python3 tools/review.py list --kind upgrade
   ```
   An in-band upgrade or a normal outreach email is `pending_review`. An
   over-band or under-band surcharge is `needs_human` - read
   `docs/safety.md` before approving one of these.

4. **Work the queue.** `workflows/80-review.md` covers approve / edit /
   reject / send in full.

5. **Keep it running.**
   ```bash
   make watch                          # loop on the configured interval
   ```
   Or schedule it - `make schedule` and `scheduler/` have cron, launchd and
   systemd examples. `config/agent.yaml`'s `schedule.outreach` /
   `schedule.upgrade_scan` document the cadence this repo was built around
   (mornings).

## Edge cases

- **No new arrivals in either window.** `make run` prints `0` for both
  tracks and exits 0. Nothing to do.
- **A reservation is a candidate for both tracks.** Normal - Track A and
  Track B each keep their own `items` row per reservation
  (`kind="outreach"` / `kind="upgrade"`), and dedup is per kind, so this is
  not a double-count.
- **A guest actually replies.** Neither track parses email automatically -
  a human reads the reply and runs `tools/outreach.py respond` or
  `tools/upgrade.py respond`. See the two `2x-*` workflows.
- **`--dry-run` shows numbers but nothing shows up in `make review`.** That
  is correct - dry-run never writes, not even the dedup row
  (`docs/how-it-works.md`).

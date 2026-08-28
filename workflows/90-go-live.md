# Workflow: shadow to live

Objective: decide, together with the hotel, whether Upsell AI is ready to
send approved drafts and execute approved upgrades on its own instead of
only queuing them - and make the change safely if so.

This is the hotel's decision, never the agent's. Do not suggest it until the
checklist below is genuinely true, and when you do raise it, say plainly what
changes.

## Checklist

- [ ] `make doctor` is clean (no `FAIL` lines). `warn` on `mode` is expected
      until you flip it.
- [ ] `config/hotel.yaml` has the real property name, address and contact
      details.
- [ ] `config/agent.yaml`'s `room_ladder` matches your real room type names
      exactly, and `offers:` is your real catalogue, not the starter one.
- [ ] At least a week of real `make run` passes have gone through the review
      queue - both tracks - not just the demo fixtures.
- [ ] You've watched a real over-band or under-band `needs_human` upgrade
      draft and are comfortable with how it reads before trusting the band
      numbers in `surcharge_band`.
- [ ] At least one full upgrade cycle - offer sent, guest replied, `respond`,
      `execute` - has been walked through by a human, so the two-approval
      shape in `docs/how-it-works.md` makes sense to whoever runs this.
- [ ] The hotel has decided on, and added, the AI-disclosure line to
      `knowledge/signature.md` (`docs/safety.md` has suggested wording and
      the EU AI Act Article 50 context).
- [ ] A real mailbox and PMS are connected (`systems.email.adapter` /
      `systems.pms.adapter` not `mock`) and `make doctor` shows both healthy.

## Making the change

1. Edit `config/hotel.yaml`:
   ```yaml
   mode: live
   ```
2. `review.require_approval_for` still lists `send_email` and `pms_write` by
   default - it should. Going live means **approved drafts get sent and
   approved executions run**, not that either track starts acting
   unapproved. There is no config that changes that for an over-band or
   under-band surcharge.
3. Clear the shadow-era backlog:
   ```bash
   python3 tools/review.py stale
   ```
   Everything still `pending_review`, `needs_human`, `approved` or `edited`
   from before this moment moves to `stale`. None of it was ever sent -
   shadow blocked every send, approved or not - but it may be out of date by
   now (an offer priced off yesterday's rates, an outreach email for a guest
   who has since checked in). `stale` is a dead end on purpose: neither
   track's scan ever reconsiders an existing item once it has left `new`
   (see `tools/outreach.py` / `tools/upgrade.py` STATE_SKIP_REASON), so a
   stale item stays exactly as it is unless a human moves it. If one still
   genuinely matters, ask your Claude session to move it back with
   `core.store.Store.transition(item_id, "pending_review", "human")` so it
   reappears in `make review`; there is no dedicated CLI command for this,
   deliberately - reviving a stale item should be rare enough to say out
   loud, not routine enough to script.
4. Run `make doctor` again to confirm.
5. Run one real pass and manually watch a send and an execute go through:
   ```bash
   python3 tools/run.py --once --limit 1
   python3 tools/review.py list
   python3 tools/review.py approve <id>
   python3 tools/review.py send
   ```
6. Tell the hotel exactly what just changed: an approved outreach or upgrade
   email now actually leaves the mailbox, and an approved `upgrade_execute`
   item now actually writes to the PMS, the next time someone (or a
   scheduled job) runs the relevant command. Nothing sends or writes before
   that approval, live mode or not.

## Going back to shadow

```yaml
mode: shadow
```
in `config/hotel.yaml`, or `AGENT_MODE=shadow` in `.env` for one run. Either
stops every outbound action and every PMS write on the next pass, with no
other change required.

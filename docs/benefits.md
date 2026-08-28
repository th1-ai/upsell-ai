# Measuring the benefit

## The business case (from the roster, verbatim)

**Upsell AI.** "Pre-arrival upsells add EUR 20-60 ancillary spend per
booking; paid room upgrades alone add ~1.55% total revenue = ~10% profit."
ROI: **+10%** - "Profit from upsells & upgrades."

**Room Upgrade AI ("The Closer").** "~1.55% uplift in total revenue = ~10%
increase in profit."

**Pre-Arrival Outreach AI ("The Welcomer").** "Pre-arrival upsell adds EUR
20-60 ancillary spend per booking and lifts review scores."

**Email Optimizer / Coach AI.** "Drives the human-edit rate down week over
week; agents graduate to full autonomy as their edit rate falls below 10%."

## Why this particular shape earns it

A guest who has already booked is the easiest sale in the building, and most
hotels never ask - see `docs/how-it-works.md` for exactly how each track
decides who to ask, with what, and at what price. Two things make the number
real rather than aspirational:

1. **The price guard and the surcharge band are not decoration.** They are
   why a hotel can actually turn this on: every offer and every upgrade
   surcharge is provably inside a band a manager set, so approving a batch of
   drafts is a five-minute skim, not a re-negotiation of every line.
2. **Nothing is auto-booked.** Every email ends with permission language.
   The revenue only exists because a guest said yes, which is also why the
   number is honest - `make report` counts *accepted* value, not sent value.

## What to measure

```bash
make report
```

- **Ancillary revenue accepted** (Track A) - sum of `accepted_value` on
  `outreach` items whose `response.outcome == "accepted"`.
- **Upgrade surcharge accepted** (Track B) - sum of `total_delta` on
  `upgrade` items with an accepted response, which is also what actually
  lands on the folio once `tools/upgrade.py execute` runs.
- **How much is sitting in the queue** - `pending_review` + `needs_human`
  counts per kind; a queue that never empties is a sign the review cadence,
  not the drafting, needs attention.
- **Coach suggestions waiting** - `python3 tools/coach.py list`; a growing
  backlog with nothing accepted means the suggestions aren't specific enough
  to act on, or nobody has time to - either is worth knowing.
- **LLM spend** - `make report` also prints `core.store.Store.usage_totals()`.
  Both tracks are LLM-free by design (see `docs/how-it-works.md`), so this
  should stay near zero except for the coach's weekly run and the cosmetic
  narrative line.

## Honest caveats

- **The 40% / EUR 10-100 / 55% numbers are starting points, not laws of
  physics.** They came from one property's demo data. Watch `make report`
  for a few weeks and adjust `config/agent.yaml` - a hotel with a wider rack
  ladder will need a wider surcharge band, or the top tier will never clear
  the guard.
- **This does not take payment.** The confirmation email says a card was
  charged; nothing in this repo charges one. Wire a real payment adapter
  before you rely on the surcharge landing on its own - see
  `docs/integrations.md`.
- **The +10% / +1.55% / +9% figures are the source demo's, not a guarantee
  for your property.** Room mix, rack spread, and how many guests you
  actually have profile data on all move the real number, up or down.
- **The coach measures itself honestly.** `edit rate < 10%` is a target the
  roster names, not a number this code enforces - there is no autonomy gate
  tied to it. Track it in `make report` and decide for yourself when a track
  has earned less review.

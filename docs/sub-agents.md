# Sub-agents in this repo

Upsell AI ("The Merchant") is an umbrella - on the demo platform it has no
engine of its own, and neither does this repo. Its whole promise, "the whole
upsell journey around every booking", is the sum of the two tracks below.
That is why **both ship on by default**: turning both off leaves nothing
running at all.

## Room Upgrade AI - "The Closer" (`tools/upgrade.py`)

**Adds:** the one upsell that changes the reservation itself - scans the
book for guests in lower-tier rooms, prices a paid move up the ladder inside
a guardrail band, and on acceptance genuinely re-rooms the guest, adjusts the
total, and writes the PMS note.

**Config:** `subagents.room_upgrade` in `config/agent.yaml` -
`room_ladder`, `window_days`, `upgrade_factor`, `surcharge_band`,
`group_exclude`, `offer_valid_hours`.

**Enable/disable:**

```yaml
subagents:
  room_upgrade:
    enabled: false   # Track A keeps running on its own
```

**Workflow:** `workflows/22-room-upgrades.md`.

## Pre-Arrival Outreach AI - "The Welcomer" (`tools/outreach.py`)

**Adds:** the timing and relevance layer - a ranked pass over every arrival
inside the outreach window, matched offers with a `because` trace for each
one, the double-mail guard, and a check-in mention for close-in arrivals.

**Config:** `subagents.pre_arrival_outreach` in `config/agent.yaml` -
`outreach_window_days`, `min_days_before_arrival`, `checkin_link_days`,
`rank_vip_first`, `match_profile`, `price_guard`, `price_guard_share`,
`max_paid_offers`, `max_repeat_offers`.

**Enable/disable:**

```yaml
subagents:
  pre_arrival_outreach:
    enabled: false   # Track B keeps running on its own
```

**Workflow:** `workflows/21-pre-arrival-outreach.md`.

## How they talk to each other

The only link between the two tracks is the cross-link skip: an arrival with
an upgrade offer that has actually been sent and not yet answered is left
alone by Track A until that's resolved (`docs/how-it-works.md` step 4). There
is no other coupling - each writes its own `items` rows (`kind="outreach"` /
`kind="upgrade"` / `kind="upgrade_execute"`), so disabling either one does not
touch the other's data, and re-enabling it later picks up exactly where it
left off (dedup is per reservation, not per run).

## Coach - Email Optimizer / Coach AI (`tools/coach.py`)

Not a "sub-agent" in the roster's sense (it has no card of its own on the
demo platform, it *applies to* four agents including this one), but it ships
here the same way: `config/agent.yaml`'s `coach.enabled`, its own workflow
(`workflows/85-coach-weekly.md`), and its own table (`coach_suggestions`, via
`store.migrate()`). See `docs/how-it-works.md` for what it does and does not
do - in short, it clusters your own edits into patterns and suggests a fix
for a human to apply; it never talks to guests and nothing is auto-applied.

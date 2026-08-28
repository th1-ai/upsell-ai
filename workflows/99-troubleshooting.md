# Workflow: troubleshooting

Read the whole error before doing anything - every tool here says what broke
and what to do about it. If you fix something not covered below, add it here.

## `make doctor` shows a FAIL

Each `FAIL` line has a `->` fix hint right under it. Common ones:

- **`hotel identity`: name is still 'Hotel Aurora'.** Expected on a fresh
  clone. Edit `config/hotel.yaml`.
- **`room ladder`: only N room type(s) configured.** Needs at least two
  tiers in `config/agent.yaml`'s `room_ladder`, matching your PMS room type
  names exactly.
- **`offer catalogue`: no offers configured.** Copy
  `config/agent.example.yaml` to `config/agent.yaml` - it ships with a
  starter catalogue.
- **`sub-agents`: both tracks are disabled.** The parent has no engine of
  its own (`docs/sub-agents.md`) - enable at least one.
- **An adapter shows FAIL, not warn.** `universal`/`built` adapters fail
  loud when misconfigured; a `warn` is reserved for stubs.

## `make demo` does not print `DEMO OK`

- Make sure `make setup` ran first (`.venv` must exist).
- `tools/demo.py` runs "as of" 2026-09-01, matching
  `fixtures/hotel/reservations.json` - if you edited that file, the fixed
  numbers in the demo's own commentary will drift, but `DEMO OK` should
  still print with whatever counts your edit produced.
- Read the traceback if there is one; `tools/demo.py` does not swallow
  errors on purpose.

## `make run` exits with code 3

Not an error. `llm.provider: interactive` parked the cosmetic run-summary
prompt. Read `data/pending/*.prompt.md`, write your answer to the matching
`*.answer.json`, and run the same command again - or ignore it, the summary
line is never required.

## A reservation isn't getting an outreach email or an upgrade offer

Run the scan directly and read the skip reason:

```bash
python3 tools/outreach.py scan
python3 tools/upgrade.py scan
```

Both print every skip with its reason. Common ones: outside the window
(check `outreach_window_days` / `window_days`), already asked (one item per
reservation, forever - see `docs/how-it-works.md`), a Group channel or an
in-house guest (Track B only), or no availability in the target room type
for even one night of the stay.

## An offer is stuck at `sending`

A process died between claiming an item and finishing the send.
`tools/run.py` calls `core.store.Store.reap_stuck_sending()` on every pass,
which moves anything stuck for more than 30 minutes to `failed`. Use
`python3 tools/review.py retry <id>` once the cause is fixed.

## The offer email or the price is wrong

Fix it in the review queue first (`edit`, not `reject`, so the correction is
recorded as a `learnings` row for the coach), then look at whether
`config/agent.yaml` needs a different `match_keys` list, a different
`upgrade_factor`, or a wider `surcharge_band`. `tools/outreach.py` and
`tools/upgrade.py` hold the template text if the wording itself needs a
structural change.

## `python3 tools/*.py` says `ModuleNotFoundError: No module named 'core'`

You ran it with a Python that is not the repo's virtualenv, or from outside
the repo root. Use `make run` / `make doctor` / etc. (they call
`.venv/bin/python` for you), or run `.venv/bin/python tools/run.py`
directly from the repo root.

## Still stuck

`data/logs/*.jsonl` has every decision the agent made, in order, with a run
id. `python3 tools/review.py show <id>` has the full event trail for one
item. If neither explains it, that is a real bug - describe exactly what you
ran and what you expected, and ask.

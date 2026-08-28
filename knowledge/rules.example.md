# Coach suggestions

This file is a plain running checklist, not a live prompt - Track A and
Track B are deliberately LLM-free (see `docs/how-it-works.md`), so nothing
here changes what they do automatically. `python3 tools/coach.py accept <id>`
appends a line below whenever you accept a suggestion; act on each one by
hand in `config/agent.yaml` (an offer's price or `match_keys`, the
`price_guard_share`, the `surcharge_band`, the `room_ladder`) or in
`knowledge/property.md`, then tick it off.

Copy this file to `knowledge/rules.md` (or let `tools/coach.py accept` create
it for you on first use) to start your own list.

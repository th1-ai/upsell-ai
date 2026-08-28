# Hotel Aurora - demo fixture

42-room seaside hotel in Lisbon, Portugal. Four room tiers, low to high:
Classic Room, Deluxe Sea View, Junior Suite, Meridian Suite (matches
`config/agent.example.yaml`'s `room_ladder`). Currency EUR. Matches
`config/hotel.example.yaml`.

**Anchor date.** Every fixture in this folder is dated relative to
**2026-09-01**. `make demo` and the tests run "as of" that date
(`tools/demo.py`'s `DEMO_AS_OF`, `--as-of` on `tools/run.py` /
`tools/outreach.py` / `tools/upgrade.py`) instead of the real today, so the
output is identical no matter when you actually run it - see
docs/how-it-works.md, "Design decisions" #1.

This file is fixture data for `make demo` and `tests/`, not the knowledge base
the agent reasons over - that is `knowledge/property.md` (see
`knowledge/README.md`). Track A and B are deliberately LLM-free, so neither
one reads this file at runtime; it exists for a human (or a test) to check
the fixtures against.

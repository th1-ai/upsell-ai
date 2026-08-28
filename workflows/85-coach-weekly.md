# Workflow: the weekly coach pass

Objective: turn a week of your own edits and rejections into a short list of
concrete improvements, and decide what to do with each one.

This never touches a guest and never changes either engine's behavior on
its own - see `docs/how-it-works.md`, "The coach", and `docs/safety.md`.

## Inputs

- `coach.enabled` in `config/agent.yaml`.
- A week's worth of `python3 tools/review.py edit` / `reject` calls -
  every one already wrote a before/after pair into `core.store`'s
  `learnings` table with no extra setup.

## Steps

1. **Run it.**
   ```bash
   python3 tools/coach.py run
   ```
   Groups every learning recorded since the last run by what it was for
   (`applied_to` - `outreach` or `upgrade`), and writes one suggestion per
   group.

2. **Read the suggestions.**
   ```bash
   python3 tools/coach.py list
   ```
   Each line names the cluster, how many edits fed it, and the suggestion
   itself. A good one names a specific `config/agent.yaml` value (an offer's
   price, its `match_keys`, `price_guard_share`, `surcharge_band`,
   `room_ladder`) or a fact missing from `knowledge/property.md`.

3. **Decide, per suggestion.**
   ```bash
   python3 tools/coach.py accept <id>     # appends it to knowledge/rules.md
   python3 tools/coach.py dismiss <id>    # discards it
   ```
   `accept` only writes to `knowledge/rules.md` - a checklist for a human (or
   this Claude session) to act on. It does not touch `config/agent.yaml`
   itself. Read the accepted line, make the actual edit, then note that you
   did.

4. **Apply the ones worth applying**, by hand, in `config/agent.yaml` or
   `knowledge/property.md`, then re-run `make demo` or a real `make run` to
   confirm the change did what you expected.

## Edge cases

- **No learnings since the last run.** `coach run` prints `0 new learning(s)`
  and writes nothing. Nothing to do - keep editing drafts in the review
  queue, the material builds up on its own.
- **A suggestion is too vague to act on.** Dismiss it and, if it keeps
  happening, tighten `prompts/coach-suggest.md` - it is plain markdown, edit
  it directly.
- **`llm.provider` fails or is rate-limited mid-run.** Never blocks: the
  cluster gets the fixed fallback suggestion ("add a rule to
  config/agent.yaml or a fact to knowledge/property.md...") instead of an
  error, and the run still records the cursor so nothing is re-processed
  next time.
- **On `llm.provider: interactive`, your answer in
  `data/pending/<id>.answer.json` does not match the schema** (for example,
  a `suggestion` longer than the schema's `maxLength`). This is different
  from a provider failure and is never swallowed into the fallback text:
  `coach run` prints the readable schema error and exits `1`, that cluster's
  `.prompt.md` / `.schema.json` / `.answer.json` files are left exactly
  where they are (nothing is deleted or renamed), and the cursor does not
  advance for the whole run. Fix the answer file in place and re-run
  `python3 tools/coach.py run` - it re-reads the same file.

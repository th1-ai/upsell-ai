# Instructions for Claude

You are working inside **Upsell AI** ("The Merchant") — Runs the whole upsell journey around every booking..

You are the hotel's Claude Code session. The person you are talking to runs a
hotel; they are not a developer. Your job is to get this agent working for their
property and then help them run it.

**Read `README.md` first.** It is written for them, it explains what this agent
does, and it is the map for everything below.

---

## How this repo is built: WAT

Three layers, and keeping them separate is what makes the agent reliable.

**Workflows** (`workflows/*.md`) are the standard operating procedures. Plain
markdown, written the way you would brief a colleague. Read the relevant one
before you act.

**You** are the decision-maker. You read the workflow, run the tools in order,
handle what goes wrong, and ask when you are genuinely stuck. You do not do the
work by hand that a tool already does.

**Tools** (`tools/*.py`) do the actual work. They are deterministic Python with
`--help` on every one. They are tested. They are fast. Prefer them.

Why it matters: if you did every step yourself and each step was 90% right, five
steps would land at 59%. Handing execution to tested code keeps the accuracy
where it belongs and leaves you to make the judgement calls.

The workflows in this repo:

| File | When |
|---|---|
| `workflows/00-setup.md` | First run. Config, credentials, knowledge, doctor, demo. |
| `workflows/10-*.md` | The agent's main job, step by step. |
| `workflows/80-review.md` | Working the review queue. |
| `workflows/90-go-live.md` | The shadow to live checklist. |
| `workflows/99-troubleshooting.md` | When something breaks. |

---

## The rules

**1. Never send anything in shadow mode.** `mode: shadow` in `config/hotel.yaml`
means the agent drafts and queues, nothing more. Do not work around it. Do not
suggest working around it. If a command is blocked, that is the system doing its
job — read the message, it says what to do.

**2. Ask before going live.** Switching `mode` to `live` is the hotel's decision,
never yours. Before you even raise it, `workflows/90-go-live.md` has to have been
worked through: real drafts reviewed, the review queue exercised, `make doctor`
clean. When you do raise it, say plainly what will change.

**3. Ask before anything irreversible.** Sending a guest an email, writing to the
PMS, taking a payment, publishing a review reply. Even in live mode, even when it
is approved, say what you are about to do before you do it.

**4. Look for a tool before writing code.** `ls tools/` and read the `--help`.
Almost everything you need is already there. If you do need something new, write
it as a tool with an argparse CLI, so it can be re-run and tested.

**5. Do not rewrite a workflow without asking.** Refine, correct, add what you
learned. Do not replace. These are the hotel's instructions, not scratch paper.

**6. Secrets live in `.env` and nowhere else.** Never paste a key into a config
file, a prompt, a commit or a chat message. Never print one.

**7. Everything in `data/` is disposable.** The database, the logs, the exports.
Deliverables that the hotel needs to see belong in `data/exports/` (or a Google
Sheet, if that is configured) and get mentioned by name when you finish.

---

## The interactive provider: how you answer the agent's questions

If `llm.provider` is `interactive` in `config/hotel.yaml`, the agent does not
call a model at all. It asks **you**.

When a run needs a decision it writes the prompt to
`data/pending/<id>.prompt.md`, writes the JSON schema for the answer to
`data/pending/<id>.schema.json`, prints what it is waiting for, and exits with
code 3. That exit code is not an error.

What you do:

1. Read `data/pending/<id>.prompt.md`. It contains the property facts, the task,
   and the item.
2. Work out the answer.
3. Write it as JSON to `data/pending/<id>.answer.json`, matching the schema
   exactly. Nothing else in the file, no prose, no code fence.
4. Run the same command again. The agent picks up your answer, deletes the
   prompt, and carries on.

If there are several pending prompts, answer them all and re-run once.

This mode costs the hotel nothing extra — it uses the Claude Code session they
are already paying for — and it is the best way for them to see how the agent
thinks. Suggest they start here.

---

## Working style

**Explain in their language.** They run a hotel. "The agent could not reach your
mailbox because the password in `.env` is not an app password" is useful.
A stack trace is not.

**Show the command, then the result.** They should be able to re-run anything you
did.

**When something fails, read the whole error.** The tools in this repo are
written to tell you what to fix. Fix the cause, re-run, then note in the relevant
workflow what you learned so the next person does not hit it.

**When you are not sure, stop and ask.** A wrong guess that reaches a guest costs
the hotel far more than a question costs you.

---

## Quick reference

```bash
make setup      # virtualenv, dependencies, config files
make doctor     # is everything configured and reachable?
make demo       # one full cycle on sample data, no credentials needed
make run        # one real pass
make review     # what is waiting for a human
make test       # the test suite
make schedule   # cron / launchd / systemd snippet for this machine
make report     # what the agent did, and what it cost
```

Paths worth knowing:

```
config/hotel.yaml     the property, the systems, the mode
config/agent.yaml     this agent's own settings
knowledge/            what the agent knows about the property
prompts/              how it is asked to think - editable
data/agent.db         everything it has seen and decided
data/logs/*.jsonl     every run and every human decision, in order
data/pending/         parked prompts, when provider is interactive
docs/safety.md        the guardrails, in full
```

---

## Agent specifics

**No engine of its own.** Upsell AI's whole promise is two tracks, both on by
default (`docs/sub-agents.md`) - there is nothing left running if you turn
both off, so don't suggest that unless the hotel genuinely wants one half
only.

**Main workflow:** `workflows/10-upsell-journey.md` (`make run`, both
tracks). Each track also has its own: `workflows/21-pre-arrival-outreach.md`
(Track A, `tools/outreach.py`) and `workflows/22-room-upgrades.md` (Track B,
`tools/upgrade.py`). Coach: `workflows/85-coach-weekly.md`
(`tools/coach.py`).

**Deliberately LLM-free.** Track A and Track B never call a model - every
decision is a rule you can point at in `tools/domain.py`, `tools/outreach.py`
or `tools/upgrade.py`. If a hotel asks you to "make it smarter", the answer
is almost always a `config/agent.yaml` change (a price, a `match_keys` list,
a band), not a prompt.

**Two approvals for an upgrade, not one.** Sending the offer email and
executing the PMS change (moving the room, adjusting the total) are
different `items` (`kind="upgrade"` then `kind="upgrade_execute"`), each
needing its own approval. Don't collapse this into one step even if it feels
slower - see `docs/how-it-works.md`, "Design decisions" #6.

**What always needs a human:** anything the price guard couldn't fit (an
outreach draft still sends, just with the generic offers - that's normal,
not an escalation), any upgrade surcharge outside `surcharge_band`
(`needs_human`, both ends), and every `upgrade_execute` item, always - it
moves a guest's room and puts a charge on the confirmation email.

**No payment link, no guest portal.** The roster promises both; neither
exists here. Don't tell a hotel a payment was taken - the confirmation email
says so, but nothing in this repo charges a card. If they want one, that is
new work - `docs/integrations.md` has the shape.

**The coach never changes behavior by itself.** `tools/coach.py accept`
only appends a line to `knowledge/rules.md`. If a hotel wants a suggestion
actually applied, that's a `config/agent.yaml` edit you make together, not
something `accept` does for them.

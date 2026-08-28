# Workflow: first-run setup

Objective: get Upsell AI from a fresh clone to a working demo, then to real
config, in one sitting.

## Steps

1. **Install and check.**
   ```bash
   make setup
   make doctor
   ```
   `make setup` creates the virtualenv, installs `requirements.txt`, and
   copies `.env.example` -> `.env` and every `config/*.example.yaml` ->
   `config/*.yaml` (only if those files do not exist yet). `make doctor`
   shows a `FAIL` on "hotel identity" right after setup - expected, it means
   the property is still the shipped placeholder. Everything else should be
   `ok` or `warn`.

2. **Run the demo.** No credentials needed.
   ```bash
   make demo
   ```
   Expect to see Track B (Room Upgrade) run first, one upgrade offer
   pre-seeded straight in the database as already sent (never through
   `email.send()` - shadow mode blocks every send, approved or not) purely
   to show the two tracks talking to each other, then Track A (Pre-Arrival
   Outreach), and finally
   `DEMO OK - 10 outreach drafted, 6 upgrade offer(s) drafted, 1 held
   (shadow)`. If you do not see that, stop and read
   `workflows/99-troubleshooting.md`.

3. **Fill in the property.** Edit `config/hotel.yaml` (name, address,
   contact, languages, currency). Then:
   ```bash
   cp knowledge/property.example.md knowledge/property.md
   cp knowledge/faq.example.md      knowledge/faq.md
   ```
   Neither Track A nor Track B reads these at runtime (both are deliberately
   template-only - see `docs/how-it-works.md`), but keep them current anyway:
   your own reference, and what the rest of the guest-comms family reads.

4. **Set your room ladder and offer catalogue.** Edit `config/agent.yaml`:
   - `subagents.room_upgrade.room_ladder` - your own room type names, low to
     high, **exactly** matching what your PMS calls them (or what you put in
     `data/imports/reservations.csv` if you're on the `csv` adapter).
   - `offers:` - replace the starter catalogue with what you actually sell:
     title, price, `price_unit` (`one-off` / `per person` / `per day`), and
     `match_keys` (see `docs/how-it-works.md` step 6 for what keys the
     matcher understands).
   Run `make doctor` again - it checks both are sane.

5. **Pick how the agent thinks.** Only two LLM calls exist in this whole
   repo (the cosmetic run summary and the coach's weekly suggestion - see
   `docs/how-it-works.md`), so this matters less here than in most of the
   family, but `config/hotel.yaml`'s `llm.provider` still starts as
   `interactive` - it asks you, in this Claude Code session, instead of
   calling a model. `docs/safety.md` covers the other three providers.

6. **Connect a real mailbox and PMS (optional for now).** Both start as
   `mock`, which only ever sees the bundled fixtures. `docs/integrations.md`
   covers `csv` (works with any PMS with zero API access) and `imap` (works
   with any mailbox). Run `make doctor` after changing either.

7. **Re-check.**
   ```bash
   make doctor
   ```
   Once the property name is real, `knowledge/property.md` exists, and your
   room ladder and offers match reality, move on to
   `workflows/10-upsell-journey.md`.

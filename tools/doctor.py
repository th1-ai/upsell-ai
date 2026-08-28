#!/usr/bin/env python3
"""tools/doctor.py - is Upsell AI configured and reachable right now?

    make doctor
    python3 tools/doctor.py

Runs the generic core.doctor checks (python, deps, config, .env, hotel
identity, mode, llm provider, every adapter, the store, knowledge) plus
Upsell-AI-specific ones: the offer catalogue, the room ladder, and the two
prompt files. Exits 0 when everything passed, 1 when a FAIL line needs
fixing. Never a traceback: a config error is shown as a FAIL row like any
other.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, Settings, load_settings  # noqa: E402
from core.doctor import Check, FAIL, PASS, WARN, print_table, run_checks  # noqa: E402
from tools.domain import load_offers  # noqa: E402


def check_offers(settings: Settings) -> Check:
    offers = load_offers(settings)
    if not offers:
        return Check("offer catalogue", FAIL, "no offers configured",
                     "Copy config/agent.example.yaml to config/agent.yaml - it ships "
                     "with a starter catalogue under `offers:`.")
    priced = [o for o in offers if o.price > 0]
    return Check("offer catalogue", PASS,
                 f"{len(offers)} offer(s), {len(priced)} paid, "
                 f"{len(offers) - len(priced)} free")


def check_room_ladder(settings: Settings) -> Check:
    ladder = settings.agent_get("subagents.room_upgrade.room_ladder", [])
    if len(ladder) < 2:
        return Check("room ladder", FAIL, f"only {len(ladder)} room type(s) configured",
                     "config/agent.yaml's room_ladder needs at least two tiers, low to "
                     "high, matching your PMS room type names exactly.")
    return Check("room ladder", PASS, " -> ".join(ladder))


def check_subagents(settings: Settings) -> Check:
    outreach_on = bool(settings.agent_get("subagents.pre_arrival_outreach.enabled", True))
    upgrade_on = bool(settings.agent_get("subagents.room_upgrade.enabled", True))
    if not outreach_on and not upgrade_on:
        return Check("sub-agents", WARN, "both tracks are disabled",
                     "The parent has no engine of its own - see docs/sub-agents.md. "
                     "Enable at least one in config/agent.yaml.")
    return Check("sub-agents", PASS,
                 f"pre_arrival_outreach={'on' if outreach_on else 'off'}, "
                 f"room_upgrade={'on' if upgrade_on else 'off'}")


def check_prompts() -> Check:
    missing = [p for p in ("prompts/run-narrative.md", "prompts/coach-suggest.md",
                           "prompts/schemas/run-narrative.json",
                           "prompts/schemas/coach-suggest.json")
              if not (REPO_ROOT / p).is_file()]
    if missing:
        return Check("prompts", FAIL, f"missing {', '.join(missing)}",
                     "These ship with the repo - restore them from git.")
    return Check("prompts", PASS, "run-narrative.md + coach-suggest.md + schemas present")


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        checks = run_checks(None) + [Check("config", FAIL, str(exc),
                                           "Fix config/hotel.yaml or config/agent.yaml.")]
        return print_table(checks, title="Upsell AI - doctor")

    checks = run_checks(settings, extra=[check_offers, check_room_ladder, check_subagents])
    checks.append(check_prompts())
    return print_table(checks, title="Upsell AI - doctor")


if __name__ == "__main__":
    raise SystemExit(main())

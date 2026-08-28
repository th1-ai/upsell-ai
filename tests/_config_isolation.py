"""Shared test helper: an isolated copy of config/*.example.yaml.

Several test files (test_coach.py, test_outreach_engine.py, test_upgrade_engine.py,
test_run_loop_and_review.py) need the repo's real fixtures/, prompts/ and
knowledge/*.example.md - so they cannot redirect AGENT_REPO_ROOT to an empty
tmp dir the way test_core_config.py / test_core_llm_mock.py do. But they must
never read this repo's own config/hotel.yaml or config/agent.yaml: a hotel's
own edits to those files (mode, room ladder, offers, property name...) must
never be able to turn `make test` red - see factory/workflows/build-repo.md
"Tests never read the live config".

``write_example_config_dir()`` only ever *creates* the tmp copy - it never
touches ``os.environ`` itself. Each test file points ``AGENT_CONFIG_DIR`` at
it through an autouse ``monkeypatch`` fixture (see the four files above), so
the override is scoped to that file's own tests and is cleaned up after each
one - a raw, unscoped ``os.environ`` write here would leak into every test
module that runs afterward in the same pytest session, including
test_core_config.py's tests, which depend on ``AGENT_CONFIG_DIR`` being unset
by default.

Not a `test_*.py` file, so pytest never collects it.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def write_example_config_dir() -> Path:
    """Return a fresh tmp dir holding config/*.example.yaml copied to *.yaml."""
    cfg_dir = Path(tempfile.mkdtemp(prefix="upsell-ai-test-config-"))
    for example in (_REPO_ROOT / "config").glob("*.example.yaml"):
        target = cfg_dir / example.name.replace(".example.yaml", ".yaml")
        target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    return cfg_dir

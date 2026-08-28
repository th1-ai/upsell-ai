"""Tests for the main loop (tools/run.py) and the kind-aware send queue
(tools/review.py). provider=mock, no network, no credentials.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from _config_isolation import write_example_config_dir  # noqa: E402

import pytest  # noqa: E402

from core.config import load_settings, sub_data_dir  # noqa: E402
from core.review import WriteBlocked, approve, reject  # noqa: E402
from core.store import Store  # noqa: E402
from tools import coach  # noqa: E402
from tools.review import _print_item_line, cmd_send, cmd_show  # noqa: E402
from tools.run import narrate, one_pass  # noqa: E402

DEMO_AS_OF = date(2026, 9, 1)


_EXAMPLE_CONFIG_DIR = write_example_config_dir()


@pytest.fixture(autouse=True)
def _use_example_config(monkeypatch):
    """Every test in this file must load config/*.example.yaml, never this
    repo's own config/hotel.yaml / agent.yaml - see factory/workflows/
    build-repo.md "Tests never read the live config". Scoped via monkeypatch
    so it never leaks into another test module's tests."""
    monkeypatch.setenv("AGENT_CONFIG_DIR", str(_EXAMPLE_CONFIG_DIR))


def _settings(mode="shadow"):
    return load_settings(provider="mock", mode=mode)


def _store(tmp_path, name="run.db", settings=None):
    store = Store(settings or _settings(), path=tmp_path / name)
    store.migrate(coach.SCHEMA)
    return store


def test_one_pass_drafts_both_tracks_and_never_sends(tmp_path):
    settings = _settings()
    store = _store(tmp_path)
    code, stats = one_pass(settings, store, limit=None, provider="mock", only="all",
                           today=DEMO_AS_OF, dry_run=False)
    assert code == 0
    assert stats["outreach"]["drafted"] == 11  # fresh run: only HA-1111 skips
    assert stats["upgrade"]["sent"] == 6
    assert stats["upgrade"]["held"] == 1
    assert stats["narrative"]  # the cosmetic line was produced (mock fixture or fallback)
    counts = store.counts()
    assert counts.get("sent", 0) == 0
    store.close()


def test_dry_run_computes_but_writes_nothing(tmp_path):
    settings = _settings()
    store = _store(tmp_path)
    code, stats = one_pass(settings, store, limit=None, provider="mock", only="outreach",
                           today=DEMO_AS_OF, dry_run=True)
    assert code == 0
    assert stats["outreach"]["drafted"] == 11  # candidates counted...
    assert len(store.list_items(kind="outreach", limit=100)) == 0  # ...but nothing written
    store.close()


def test_only_filters_to_a_single_track(tmp_path):
    settings = _settings()
    store = _store(tmp_path)
    _, stats = one_pass(settings, store, limit=None, provider="mock", only="upgrade",
                        today=DEMO_AS_OF, dry_run=False)
    assert "upgrade" in stats and "outreach" not in stats
    store.close()


def test_narrate_falls_back_when_nothing_matches_the_fixture(tmp_path):
    settings = _settings()
    store = _store(tmp_path)
    # An id with no matching fixtures/expected/run-narrative/*.json falls back
    # to core.llm's schema_example(), which still returns a usable string -
    # narrate() must never raise.
    note = narrate(settings, store, {"outreach": {"drafted": 3}, "upgrade": {"sent": 1, "held": 0}},
                   provider="mock")
    assert isinstance(note, str) and note
    store.close()


class _Args:
    kind = "outreach"
    limit = 5


def test_review_send_blocked_in_shadow_even_when_approved(tmp_path):
    """mode: shadow blocks every send, approved or not - see core/review.py's
    evaluate_write. `cmd_send` must not crash and must not fail the item: the
    approval stands (the item goes back to `approved`, never `failed`) so it
    is still queued and ready the moment you go live - see core/store.py
    TRANSITIONS ("sending" -> "approved") and SIMULATION.md round 2 finding
    1, which is exactly the trigger this fixes: a `failed` item here used to
    crash the next `tools/upgrade.py scan()` / `tools/outreach.py scan()`
    pass, because neither had a skip reason for `failed`. Nothing new is
    written to the outbox (the outbox is a real repo-relative file shared
    across test runs - a fresh clone has none, so this checks that no *new*
    line was appended rather than asserting the file never exists)."""
    settings = _settings()  # mode=shadow
    store = _store(tmp_path)
    one_pass(settings, store, limit=None, provider="mock", only="outreach",
            today=DEMO_AS_OF, dry_run=False)
    item = store.get_by_external("outreach", "res-101")
    approve(store, item.id)

    outbox = sub_data_dir("exports") / "sent_email.jsonl"
    before = outbox.read_text(encoding="utf-8") if outbox.exists() else ""

    code = cmd_send(store, settings, _Args())
    assert code == 1  # nothing sent this pass, even though the approval stands
    assert store.get_item(item.id).review_status == "approved"
    after = outbox.read_text(encoding="utf-8") if outbox.exists() else ""
    assert after == before  # nothing new was appended

    # And the very thing that crashed in round 2: a re-run must not choke on
    # this now-`approved` item - the whole pass completes, nothing raises.
    code2, _ = one_pass(settings, store, limit=None, provider="mock", only="outreach",
                        today=DEMO_AS_OF, dry_run=False)
    assert code2 == 0
    store.close()


def test_review_send_dispatches_outreach_by_email_in_live_mode(tmp_path):
    settings = _settings(mode="live")
    store = _store(tmp_path, name="run-live.db", settings=settings)
    one_pass(settings, store, limit=None, provider="mock", only="outreach",
            today=DEMO_AS_OF, dry_run=False)
    item = store.get_by_external("outreach", "res-101")
    approve(store, item.id)

    code = cmd_send(store, settings, _Args())
    assert code == 0
    assert store.get_item(item.id).review_status == "sent"
    outbox = sub_data_dir("exports") / "sent_email.jsonl"
    assert outbox.exists()
    last_line = outbox.read_text(encoding="utf-8").strip().splitlines()[-1]
    assert json.loads(last_line)["to"] == ["elena.kovacs@example.com"]
    store.close()


def test_one_pass_completes_with_an_item_in_every_review_status(tmp_path):
    """The full `make run` / `tools/run.py --once` regression for SIMULATION.md
    round 2 finding 1: one item per track is walked to `failed`, `stale`,
    `rejected` and `approved` before a fresh `one_pass` (both tracks, exactly
    what `make run` and the cron job `make schedule` wires in call) - the
    whole pass must complete (exit 0) with every one of those items skipped
    and untouched, never a StoreError crash partway through."""
    settings = _settings()
    store = _store(tmp_path)
    code, _ = one_pass(settings, store, limit=None, provider="mock", only="all",
                       today=DEMO_AS_OF, dry_run=False)
    assert code == 0

    # Track A (outreach): one item into each of failed / rejected / approved.
    o_failed = store.get_by_external("outreach", "res-102")
    approve(store, o_failed.id)
    store.transition(o_failed.id, "sending", actor="agent")
    store.mark_send_failed(o_failed.id, "mock adapter: connection refused")
    o_rejected = store.get_by_external("outreach", "res-103")
    reject(store, o_rejected.id, reason="wrong tone")
    o_approved = store.get_by_external("outreach", "res-106")
    approve(store, o_approved.id)

    # Track B (upgrade): one item into each of failed / rejected / approved.
    u_failed = store.get_by_external("upgrade", "res-105")
    approve(store, u_failed.id)
    store.transition(u_failed.id, "sending", actor="agent")
    store.mark_send_failed(u_failed.id, "mock adapter: connection refused")
    u_rejected = store.get_by_external("upgrade", "res-104")
    reject(store, u_rejected.id, reason="not right for this guest")
    u_approved = store.get_by_external("upgrade", "res-110")
    approve(store, u_approved.id)

    # Whatever is still pending_review/needs_human in either track goes
    # stale - covers the `stale` status for both, run after every other
    # transition above (stale -> approved is not a legal move).
    store.mark_stale(older_than_hours=-1, statuses=("pending_review", "needs_human"))

    code2, stats2 = one_pass(settings, store, limit=None, provider="mock", only="all",
                             today=DEMO_AS_OF, dry_run=False)
    assert code2 == 0  # the whole pass completed - this is what crashed in round 2
    assert stats2["outreach"]["drafted"] == 0  # every reservation already tracked
    assert stats2["upgrade"]["sent"] == 0 and stats2["upgrade"]["held"] == 0

    for item_id, expected in ((o_failed.id, "failed"), (o_rejected.id, "rejected"),
                              (o_approved.id, "approved"), (u_failed.id, "failed"),
                              (u_rejected.id, "rejected"), (u_approved.id, "approved")):
        assert store.get_item(item_id).review_status == expected
    store.close()


def test_sample_item_shows_marker_in_list_line_and_show(tmp_path, capsys):
    """core/store.py tags an item read through a mock adapter outside `make
    demo` as `_sample` (`Item.is_sample`) - a human working the real queue
    must see that at a glance, in both `list` and `show`."""
    settings = _settings()
    store = _store(tmp_path)
    item = store.upsert_item("email", "sample-marker-1", kind="outreach",
                             payload={"guest_name": "Sam Guest", "room_type": "Deluxe",
                                      "_sample": True})
    assert item.is_sample

    capsys.readouterr()
    _print_item_line(item)
    assert "[SAMPLE DATA]" in capsys.readouterr().out

    rc = cmd_show(store, SimpleNamespace(id=item.id))
    assert rc == 0
    assert "[SAMPLE DATA]" in capsys.readouterr().out
    store.close()

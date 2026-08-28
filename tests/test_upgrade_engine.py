"""Tests for Track B - tools/upgrade.py. provider=mock, no network, no
credentials. Expected numbers match a live `make demo` run - see
docs/how-it-works.md.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from _config_isolation import write_example_config_dir  # noqa: E402

import pytest  # noqa: E402

from core.adapters import get_email, get_pms  # noqa: E402
from core.config import load_settings, sub_data_dir  # noqa: E402
from core.review import WriteBlocked, approve, reject  # noqa: E402
from core.store import Store  # noqa: E402
from tools import upgrade  # noqa: E402
from tools.domain import round_to_5  # noqa: E402

DEMO_AS_OF = date(2026, 9, 1)  # see fixtures/hotel/property.md


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


def _store(tmp_path, name="upgrade.db", settings=None):
    return Store(settings or _settings(), path=tmp_path / name)


def test_round_to_5():
    assert round_to_5(44) == 45
    assert round_to_5(42) == 40
    assert round_to_5(163) == 165


def test_band_flag_checks_both_ends():
    band = {"min": 10, "max": 100}
    assert upgrade.band_flag(5, band) == "under_band"
    assert upgrade.band_flag(45, band) is None
    assert upgrade.band_flag(165, band) == "over_band"


def test_scan_prices_an_in_band_move_from_real_rates(tmp_path):
    settings = _settings()
    store = _store(tmp_path)
    upgrade.scan(settings, store, get_pms(settings), today=DEMO_AS_OF)
    item = store.get_by_external("upgrade", "res-105")
    assert item.review_status == "pending_review"
    assert item.draft["from_room"] == "Classic Room"
    assert item.draft["to_room"] == "Deluxe Sea View"
    assert item.draft["per_night"] == 45
    assert item.draft["total_delta"] == 45 * 5


def test_scan_holds_an_over_band_move_for_a_human(tmp_path):
    settings = _settings()
    store = _store(tmp_path)
    upgrade.scan(settings, store, get_pms(settings), today=DEMO_AS_OF)
    item = store.get_by_external("upgrade", "res-106")
    assert item.review_status == "needs_human"
    assert item.draft["per_night"] == 165  # over the default EUR 10-100 band


def test_ladder_only_moves_up_one_tier_and_stops_at_the_top(tmp_path):
    settings = _settings()
    store = _store(tmp_path)
    stats = upgrade.scan(settings, store, get_pms(settings), today=DEMO_AS_OF)
    assert store.get_by_external("upgrade", "res-101") is None  # Meridian - top
    reasons = {s["reservation"]: s["reason"] for s in stats["skips"]}
    assert "top of the ladder" in reasons["HA-1101"]


def test_group_channel_is_excluded(tmp_path):
    settings = _settings()
    store = _store(tmp_path)
    stats = upgrade.scan(settings, store, get_pms(settings), today=DEMO_AS_OF)
    assert store.get_by_external("upgrade", "res-107") is None
    reasons = {s["reservation"]: s["reason"] for s in stats["skips"]}
    assert "group block" in reasons["HA-1107"]


def test_availability_check_skips_when_no_rooms_free(tmp_path):
    settings = _settings()
    store = _store(tmp_path)
    stats = upgrade.scan(settings, store, get_pms(settings), today=DEMO_AS_OF)
    assert store.get_by_external("upgrade", "res-108") is None
    reasons = {s["reservation"]: s["reason"] for s in stats["skips"]}
    assert "nothing to sell tonight" in reasons["HA-1108"]


def test_window_skip_says_how_many_days_until_it_opens(tmp_path):
    settings = _settings()
    store = _store(tmp_path)
    stats = upgrade.scan(settings, store, get_pms(settings), today=DEMO_AS_OF)
    reasons = {s["reservation"]: s["reason"] for s in stats["skips"]}
    assert "enters it in 6 day(s)" in reasons["HA-1109"]


def test_in_house_guests_are_excluded(tmp_path):
    settings = _settings()
    store = _store(tmp_path)
    stats = upgrade.scan(settings, store, get_pms(settings), today=DEMO_AS_OF)
    assert store.get_by_external("upgrade", "res-113") is None
    reasons = {s["reservation"]: s["reason"] for s in stats["skips"]}
    assert "in-stay upsells belong to a person" in reasons["HA-1113"]


def test_one_ask_per_stay_dedup_on_rerun(tmp_path):
    settings = _settings()
    store = _store(tmp_path)
    pms = get_pms(settings)
    first = upgrade.scan(settings, store, pms, today=DEMO_AS_OF)
    second = upgrade.scan(settings, store, pms, today=DEMO_AS_OF)
    assert second["sent"] == 0 and second["held"] == 0
    assert second["skipped"] >= first["sent"] + first["held"]


def test_decline_is_final_and_never_re_offered(tmp_path):
    settings = _settings()
    store = _store(tmp_path)
    pms = get_pms(settings)
    upgrade.scan(settings, store, pms, today=DEMO_AS_OF)
    offer = store.get_by_external("upgrade", "res-105")
    approve(store, offer.id)
    store.transition(offer.id, "sending", actor="agent")
    store.mark_sent(offer.id, "mock-1")

    updated = upgrade.respond(store, offer.id, "decline", note="staying as booked")
    assert updated.payload["response"]["outcome"] == "declined"

    again = upgrade.scan(settings, store, pms, today=DEMO_AS_OF)
    reasons = {s["reservation"]: s["reason"] for s in again["skips"]}
    assert "a no is final" in reasons["HA-1105"]


def test_accept_creates_a_separately_approved_execute_item(tmp_path):
    settings = _settings()
    store = _store(tmp_path)
    pms = get_pms(settings)
    upgrade.scan(settings, store, pms, today=DEMO_AS_OF)
    offer = store.get_by_external("upgrade", "res-105")
    approve(store, offer.id)
    store.transition(offer.id, "sending", actor="agent")
    store.mark_sent(offer.id, "mock-1")

    updated = upgrade.respond(store, offer.id, "accept")
    execute_item = store.get_item(updated.payload["execute_item_id"])
    assert execute_item is not None
    assert execute_item.kind == "upgrade_execute"
    assert execute_item.review_status == "pending_review"


def _walk_to_approved_execute_item(settings, store, pms):
    """Scan, get the guest to accept, and approve the resulting execute item -
    everything short of actually running it. Shared by the shadow-blocked and
    live-mode tests below so they start from the identical state."""
    upgrade.scan(settings, store, pms, today=DEMO_AS_OF)
    offer = store.get_by_external("upgrade", "res-105")
    approve(store, offer.id)
    store.transition(offer.id, "sending", actor="agent")
    store.mark_sent(offer.id, "mock-1")
    accepted = upgrade.respond(store, offer.id, "accept")

    exec_item = store.get_item(accepted.payload["execute_item_id"])
    approve(store, exec_item.id)
    return store.transition(exec_item.id, "sending", actor="agent")


def test_execute_one_blocked_in_shadow_even_when_approved(tmp_path):
    """mode: shadow blocks every write, approved or not - the FSM state
    reaching `sending` (a human approved it) does not matter; see
    core/review.py's evaluate_write and SIMULATION.md finding 1."""
    settings = _settings()  # mode=shadow
    store = _store(tmp_path, settings=settings)
    pms, email = get_pms(settings), get_email(settings)
    claimed = _walk_to_approved_execute_item(settings, store, pms)

    with pytest.raises(WriteBlocked):
        upgrade.execute_one(settings, store, pms, email, claimed, DEMO_AS_OF)


def test_execute_one_writes_pms_and_sends_confirmation_in_live_mode(tmp_path):
    settings = _settings(mode="live")
    store = _store(tmp_path, name="upgrade-live.db", settings=settings)
    pms, email = get_pms(settings), get_email(settings)
    claimed = _walk_to_approved_execute_item(settings, store, pms)

    done = upgrade.execute_one(settings, store, pms, email, claimed, DEMO_AS_OF)
    assert done.review_status == "sent"

    exports = (sub_data_dir("exports") / "pms_writes.csv")
    assert exports.exists() and "update_reservation" in exports.read_text(encoding="utf-8")


def test_sweep_expired_marks_unanswered_offers_stale(tmp_path):
    settings = _settings()
    store = _store(tmp_path)
    upgrade.scan(settings, store, get_pms(settings), today=DEMO_AS_OF)
    stale = upgrade.sweep_expired(store, hours=-1)  # cutoff in the future: catches everything
    assert len(stale) >= 1
    still_open = upgrade.scan(settings, store, get_pms(settings), today=DEMO_AS_OF)
    reasons = {s["reason"] for s in still_open["skips"]}
    assert any("not asked twice" in r for r in reasons)


def test_scan_skips_every_review_status_with_a_reason_and_never_crashes(tmp_path):
    """SIMULATION.md round 2 finding 1: a `failed` item (the state a
    shadow-blocked send now avoids, but retry-then-fail-again or a real send
    error can still produce) used to have no STATE_SKIP_REASON entry, so the
    next scan tried to `upsert_unique` + `transition(..., "pending_review")`
    on it and the FSM correctly refused with a StoreError, aborting the
    entire pass. Put one item in `failed`, `stale`, `rejected` and `approved`
    (plus the `needs_human` / `pending_review` a fresh scan already leaves
    behind) and confirm a second scan completes cleanly, skips every one of
    them with a logged reason, and drafts nothing new for any of them."""
    settings = _settings()
    store = _store(tmp_path)
    pms = get_pms(settings)
    upgrade.scan(settings, store, pms, today=DEMO_AS_OF)

    failed = store.get_by_external("upgrade", "res-105")
    approve(store, failed.id)
    store.transition(failed.id, "sending", actor="agent")
    store.mark_send_failed(failed.id, "mock adapter: connection refused")

    rejected = store.get_by_external("upgrade", "res-104")
    reject(store, rejected.id, reason="not right for this guest")

    approved = store.get_by_external("upgrade", "res-110")
    approve(store, approved.id)

    # Everything still pending_review/needs_human (including res-102, our
    # target) goes stale - order matters: this must run AFTER the three
    # transitions above, or it would stale res-104/res-110 first and make
    # reject()/approve() illegal moves from "stale".
    stale = store.get_by_external("upgrade", "res-102")
    store.mark_stale(older_than_hours=-1, statuses=("pending_review", "needs_human"))

    for item in (failed, stale, rejected, approved):
        assert item is not None

    again = upgrade.scan(settings, store, pms, today=DEMO_AS_OF)  # must not raise
    reasons = {s["reservation"]: s["reason"] for s in again["skips"]}
    assert "retry" in reasons["HA-1105"]  # failed
    assert "not asked twice" in reasons["HA-1102"]  # stale
    assert "not re-drafting automatically" in reasons["HA-1104"]  # rejected
    assert "about to send" in reasons["HA-1110"]  # approved

    # None of them were touched or re-drafted.
    assert store.get_item(failed.id).review_status == "failed"
    assert store.get_item(stale.id).review_status == "stale"
    assert store.get_item(rejected.id).review_status == "rejected"
    assert store.get_item(approved.id).review_status == "approved"
    store.close()


def test_shadow_mode_never_sends(tmp_path):
    settings = _settings()
    store = _store(tmp_path)
    upgrade.scan(settings, store, get_pms(settings), today=DEMO_AS_OF)
    counts = store.counts()
    assert counts.get("sent", 0) == 0
    assert counts.get("auto_sent", 0) == 0

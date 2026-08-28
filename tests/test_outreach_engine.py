"""Tests for Track A - tools/outreach.py and the shared tools/domain.py
matcher. provider=mock, no network, no credentials. Expected numbers here
match a live `make demo` run against fixtures/hotel/reservations.json - see
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

from core.adapters import get_pms  # noqa: E402
from core.config import load_settings  # noqa: E402
from core.review import approve, reject  # noqa: E402
from core.store import Store  # noqa: E402
from tools import outreach, upgrade  # noqa: E402
from tools.domain import GENERIC_OFFER_IDS, load_offers, match_offers, nightly_rate, signal_keys  # noqa: E402

DEMO_AS_OF = date(2026, 9, 1)  # see fixtures/hotel/property.md


_EXAMPLE_CONFIG_DIR = write_example_config_dir()


@pytest.fixture(autouse=True)
def _use_example_config(monkeypatch):
    """Every test in this file must load config/*.example.yaml, never this
    repo's own config/hotel.yaml / agent.yaml - see factory/workflows/
    build-repo.md "Tests never read the live config". Scoped via monkeypatch
    so it never leaks into another test module's tests."""
    monkeypatch.setenv("AGENT_CONFIG_DIR", str(_EXAMPLE_CONFIG_DIR))


def _settings():
    return load_settings(provider="mock", mode="shadow")


def _store(tmp_path, name="outreach.db"):
    return Store(_settings(), path=tmp_path / name)


def test_signal_keys_never_invents_a_preference_from_party_size():
    # res-103 (Priya): 2 adults, 0 children, no occasion, no profile - a bare
    # headcount must never become a "couples" pitch. docs/how-it-works.md
    # step 6, "nothing invented".
    settings = _settings()
    res = get_pms(settings).get_reservation("res-103")
    assert signal_keys(res) == set()


def test_repeat_offers_are_guard_exempt_and_take_priority():
    settings = _settings()
    res = get_pms(settings).get_reservation("res-101")  # Elena: 2 premium repeats
    chosen, swaps = match_offers(res, load_offers(settings))
    assert {row["offer"].id for row in chosen} == {"of-chefs-table", "of-cabana"}
    assert swaps == 0
    assert "repeat guests rebook" in chosen[0]["because"]


def test_specialist_beats_crowd_pleaser_on_a_tie():
    settings = _settings()
    res = get_pms(settings).get_reservation("res-102")  # Marcus: dog + spa
    chosen, _ = match_offers(res, load_offers(settings))
    ids = [row["offer"].id for row in chosen]
    assert "of-pet-package" in ids  # repeat, guard-exempt
    assert "of-dog-sitting" in ids  # 2-key hit beats a 1-key spa-only match


def test_price_guard_swaps_down_to_a_cheaper_match():
    settings = _settings()
    res = get_pms(settings).get_reservation("res-105")  # Sara: Classic Room, anniversary
    assert nightly_rate(res) == 260.0
    chosen, swaps = match_offers(res, load_offers(settings))
    assert {row["offer"].id for row in chosen} == {"of-sparkling-flowers"}
    assert swaps == 3  # couples ritual, chef's table and cellar dinner all guarded out


def test_blank_profile_gets_the_two_generic_offers_only():
    settings = _settings()
    res = get_pms(settings).get_reservation("res-112")
    chosen, _ = match_offers(res, load_offers(settings))
    assert {row["offer"].id for row in chosen} == set(GENERIC_OFFER_IDS)
    assert all("nothing invented" in row["because"] for row in chosen)


def test_family_match_from_an_explicit_party_description():
    settings = _settings()
    res = get_pms(settings).get_reservation("res-110")  # Novak family
    chosen, _ = match_offers(res, load_offers(settings))
    assert {row["offer"].id for row in chosen} == {"of-babysitting"}


def test_scan_ranks_and_skips_already_contacted(tmp_path):
    settings = _settings()
    store = _store(tmp_path)
    stats = outreach.scan(settings, store, get_pms(settings), today=DEMO_AS_OF)
    assert stats["in_window"] == 12
    assert stats["drafted"] == 11
    reasons = {s["reservation"]: s["reason"] for s in stats["skips"]}
    assert "never double-mail" in reasons["HA-1111"].lower()
    store.close()


def test_scan_skips_a_reservation_with_a_sent_open_upgrade_offer(tmp_path):
    settings = _settings()
    store = _store(tmp_path)
    pms = get_pms(settings)
    upgrade.scan(settings, store, pms, today=DEMO_AS_OF)
    offer = store.get_by_external("upgrade", "res-105")
    approve(store, offer.id)
    store.transition(offer.id, "sending", actor="agent")
    store.mark_sent(offer.id, "mock-1")

    stats = outreach.scan(settings, store, pms, today=DEMO_AS_OF)
    reasons = {s["reservation"]: s["reason"] for s in stats["skips"]}
    assert "no cross-selling mid-thread" in reasons["HA-1105"].lower()
    assert stats["drafted"] == 10  # 12 in window, minus HA-1111 and HA-1105
    store.close()


def test_a_still_pending_upgrade_offer_does_not_block_outreach(tmp_path):
    """A draft only waiting on a human's own approval is not "open" yet -
    docs/how-it-works.md step 4."""
    settings = _settings()
    store = _store(tmp_path)
    pms = get_pms(settings)
    upgrade.scan(settings, store, pms, today=DEMO_AS_OF)
    assert store.get_by_external("upgrade", "res-104").review_status == "pending_review"

    stats = outreach.scan(settings, store, pms, today=DEMO_AS_OF)
    reasons = {s["reservation"] for s in stats["skips"]}
    assert "HA-1104" not in reasons
    store.close()


def test_rerun_is_idempotent(tmp_path):
    settings = _settings()
    store = _store(tmp_path)
    pms = get_pms(settings)
    first = outreach.scan(settings, store, pms, today=DEMO_AS_OF)
    second = outreach.scan(settings, store, pms, today=DEMO_AS_OF)
    assert second["drafted"] == 0
    assert second["skipped"] == first["in_window"]
    assert len(store.list_items(kind="outreach", limit=100)) == first["drafted"]
    store.close()


def test_scan_skips_every_review_status_with_a_reason_and_never_crashes(tmp_path):
    """Track A's mirror of tools/upgrade.py's identical regression - see
    SIMULATION.md round 2 finding 1. `scan()`'s dedup already never crashed
    (any non-"new" status was skipped outright), but it must also give a
    readable reason for `failed` / `stale` / `rejected` / `approved`, not
    just a bare count, and a second scan must complete without raising."""
    settings = _settings()
    store = _store(tmp_path)
    pms = get_pms(settings)
    outreach.scan(settings, store, pms, today=DEMO_AS_OF)

    failed = store.get_by_external("outreach", "res-102")
    approve(store, failed.id)
    store.transition(failed.id, "sending", actor="agent")
    store.mark_send_failed(failed.id, "mock adapter: connection refused")

    rejected = store.get_by_external("outreach", "res-103")
    reject(store, rejected.id, reason="wrong tone")

    approved = store.get_by_external("outreach", "res-106")
    approve(store, approved.id)

    # Everything still pending_review (including res-104, our stale target)
    # goes stale - must run after the three transitions above.
    stale = store.get_by_external("outreach", "res-104")
    store.mark_stale(older_than_hours=-1, statuses=("pending_review",))

    again = outreach.scan(settings, store, pms, today=DEMO_AS_OF)  # must not raise
    reasons = {s["reservation"]: s["reason"] for s in again["skips"]}
    assert "retry" in reasons["HA-1102"]  # failed
    assert "not asked twice" in reasons["HA-1104"]  # stale
    assert "not re-drafting automatically" in reasons["HA-1103"]  # rejected
    assert "about to send" in reasons["HA-1106"]  # approved

    assert store.get_item(failed.id).review_status == "failed"
    assert store.get_item(stale.id).review_status == "stale"
    assert store.get_item(rejected.id).review_status == "rejected"
    assert store.get_item(approved.id).review_status == "approved"
    store.close()


def test_shadow_mode_never_sends(tmp_path):
    settings = _settings()
    store = _store(tmp_path)
    outreach.scan(settings, store, get_pms(settings), today=DEMO_AS_OF)
    counts = store.counts()
    assert counts.get("sent", 0) == 0
    assert counts.get("auto_sent", 0) == 0
    store.close()


def test_respond_records_the_guests_answer(tmp_path):
    settings = _settings()
    store = _store(tmp_path)
    outreach.scan(settings, store, get_pms(settings), today=DEMO_AS_OF)
    item = store.get_by_external("outreach", "res-101")
    offer_ids = [o["id"] for o in item.draft["offers"]]

    accepted = outreach.respond(store, item.id, accept_ids=offer_ids)
    assert accepted.payload["response"]["outcome"] == "accepted"
    assert accepted.payload["response"]["accepted_value"] > 0

    other = store.get_by_external("outreach", "res-112")
    declined = outreach.respond(store, other.id, accept_ids=[])
    assert declined.payload["response"]["outcome"] == "declined"
    store.close()

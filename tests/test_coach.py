"""Tests for tools/coach.py: clustering, suggestions, accept/dismiss.
provider=mock, no network, no credentials.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from _config_isolation import write_example_config_dir  # noqa: E402

import pytest  # noqa: E402

from core.config import load_settings, sub_data_dir  # noqa: E402
from core.llm import LLMPendingInteractive  # noqa: E402
from core.store import Store  # noqa: E402
from tools import coach  # noqa: E402


_EXAMPLE_CONFIG_DIR = write_example_config_dir()


@pytest.fixture(autouse=True)
def _use_example_config(monkeypatch):
    """Every test in this file must load config/*.example.yaml, never this
    repo's own config/hotel.yaml / agent.yaml - see factory/workflows/
    build-repo.md "Tests never read the live config". Scoped via monkeypatch
    so it never leaks into another test module's tests."""
    monkeypatch.setenv("AGENT_CONFIG_DIR", str(_EXAMPLE_CONFIG_DIR))


def _settings(provider="mock"):
    return load_settings(provider=provider, mode="shadow")


def _store(tmp_path):
    store = Store(_settings(), path=tmp_path / "coach.db")
    store.migrate(coach.SCHEMA)
    return store


def test_cluster_learnings_groups_by_applied_to():
    rows = [
        {"applied_to": "outreach", "before": "a", "after": "b", "lesson": "x"},
        {"applied_to": "outreach", "before": "c", "after": "d", "lesson": "y"},
        {"applied_to": "upgrade", "before": "e", "after": "f", "lesson": "z"},
        {"applied_to": None, "before": "g", "after": "h", "lesson": "w"},
    ]
    clusters = coach.cluster_learnings(rows)
    assert len(clusters["outreach"]) == 2
    assert len(clusters["upgrade"]) == 1
    assert len(clusters["general"]) == 1  # None -> "general", never dropped


def test_run_produces_one_suggestion_per_cluster(tmp_path):
    settings = _settings()
    store = _store(tmp_path)
    store.record_learning(source_item="i1", before="Dear guest,", after="Dear Elena,",
                          lesson="use the first name", applied_to="outreach")
    store.record_learning(source_item="i2", before="EUR 165/night", after="EUR 150/night",
                          lesson="round differently", applied_to="upgrade")

    stats = coach.run(settings, store, provider="mock")
    assert stats["learnings_seen"] == 2
    assert stats["clusters"] == 2
    assert stats["suggestions"] == 2

    rows = coach.list_suggestions(store)
    assert {r["applied_to"] for r in rows} == {"outreach", "upgrade"}
    assert all(r["status"] == "new" for r in rows)
    store.close()


def test_run_only_sees_learnings_since_the_last_run(tmp_path):
    settings = _settings()
    store = _store(tmp_path)
    store.record_learning(source_item="i1", before="a", after="b", lesson="x",
                          applied_to="outreach")
    first = coach.run(settings, store, provider="mock")
    assert first["learnings_seen"] == 1

    second = coach.run(settings, store, provider="mock")
    assert second["learnings_seen"] == 0
    assert second["clusters"] == 0
    store.close()


def test_accept_appends_to_knowledge_rules_and_dismiss_does_not(tmp_path, monkeypatch):
    settings = _settings()
    store = _store(tmp_path)
    store.record_learning(source_item="i1", before="a", after="b", lesson="x",
                          applied_to="outreach")
    coach.run(settings, store, provider="mock")
    accept_id = coach.list_suggestions(store, status="new")[0]["id"]

    rules_path = REPO_ROOT / "knowledge" / "rules.md"
    existed_before = rules_path.exists()
    before_text = rules_path.read_text(encoding="utf-8") if existed_before else ""
    try:
        data = coach.accept(store, accept_id)
        assert Path(data["rules_path"]).exists()
        assert data["suggestion"] in Path(data["rules_path"]).read_text(encoding="utf-8")
        assert coach.list_suggestions(store, status="accepted")[0]["id"] == accept_id
    finally:
        if existed_before:
            rules_path.write_text(before_text, encoding="utf-8")
        else:
            rules_path.unlink(missing_ok=True)
    store.close()


def test_dismiss_leaves_no_trace_in_knowledge(tmp_path):
    settings = _settings()
    store = _store(tmp_path)
    store.record_learning(source_item="i1", before="a", after="b", lesson="x",
                          applied_to="upgrade")
    coach.run(settings, store, provider="mock")
    suggestion_id = coach.list_suggestions(store, status="new")[0]["id"]
    coach.dismiss(store, suggestion_id)
    dismissed = [r for r in coach.list_suggestions(store) if r["id"] == suggestion_id][0]
    assert dismissed["status"] == "dismissed"
    store.close()


def test_interactive_provider_parks_a_real_prompt_never_the_fallback(tmp_path):
    """SIMULATION.md finding 2: `LLMPendingInteractive` is not an `LLMError`
    (core/llm.py), so it must propagate out of `run()` as a pend, not get
    silently swallowed into `coach.FALLBACK`. The cursor must not advance
    while a cluster is still pending, and answering it must produce the
    real suggestion, not the generic fallback string.
    """
    applied_to = "test-interactive-coach"
    pending_dir = sub_data_dir("pending")
    stem = f"coach-suggest-cluster-{applied_to}"
    leftovers = list(pending_dir.glob(f"{stem}.*")) if pending_dir.exists() else []
    for leftover in leftovers:
        leftover.unlink(missing_ok=True)

    settings = _settings(provider="interactive")
    store = _store(tmp_path)
    store.record_learning(source_item="i1", before="Dear guest,", after="Dear Elena,",
                          lesson="use the first name", applied_to=applied_to)
    try:
        stats = coach.run(settings, store, provider="interactive")
        assert stats["suggestions"] == 0
        assert len(stats["pending"]) == 1
        assert stem in stats["pending"][0]
        assert coach.list_suggestions(store) == []  # no canned FALLBACK either
        assert store.get_cursor("coach:last_run") is None  # not advanced while pending
        assert (pending_dir / f"{stem}.prompt.md").exists()

        answer_path = pending_dir / f"{stem}.answer.json"
        answer_path.write_text(
            '{"suggestion": "Always use the guest\'s first name in the salutation."}',
            encoding="utf-8")
        stats2 = coach.run(settings, store, provider="interactive")
        assert stats2["pending"] == []
        assert stats2["suggestions"] == 1
        rows = coach.list_suggestions(store, status="new")
        assert rows[0]["suggestion"] == "Always use the guest's first name in the salutation."
        assert rows[0]["suggestion"] != coach.FALLBACK
        assert store.get_cursor("coach:last_run") is not None
    finally:
        for leftover in pending_dir.glob(f"{stem}.*"):
            leftover.unlink(missing_ok=True)
        store.close()


def test_interactive_answer_failing_schema_surfaces_the_error_not_the_fallback(tmp_path):
    """SIMULATION.md round 2 finding 2: a schema-invalid interactive answer
    (here, a `suggestion` longer than the schema's `maxLength: 400`) must
    surface as a readable error - not get caught by `run()`'s generic
    `except LLMError: suggestion = FALLBACK` and silently written as if it
    were a real answer. It also must not consume the `coach:last_run`
    cursor (the cluster's learnings must be re-offered, not skipped
    forever) and must not delete/rename the pending `.prompt.md` /
    `.schema.json` / `.answer.json` files - the operator fixes the answer
    file in place and re-runs, mirroring tools/run.py's `LLMSchemaError`
    handling (core/llm.py: `main()`'s `except LLMError as exc: print(f"error:
    {exc}")`, not a fallback).
    """
    applied_to = "test-interactive-coach-schema"
    pending_dir = sub_data_dir("pending")
    stem = f"coach-suggest-cluster-{applied_to}"
    for leftover in (pending_dir.glob(f"{stem}.*") if pending_dir.exists() else []):
        leftover.unlink(missing_ok=True)

    settings = _settings(provider="interactive")
    store = _store(tmp_path)
    store.record_learning(source_item="i1", before="Dear guest,", after="Dear Elena,",
                          lesson="use the first name", applied_to=applied_to)
    try:
        first = coach.run(settings, store, provider="interactive")
        assert len(first["pending"]) == 1  # parks the prompt, same as any other cluster
        assert first["errors"] == []

        prompt_path = pending_dir / f"{stem}.prompt.md"
        schema_path = pending_dir / f"{stem}.schema.json"
        answer_path = pending_dir / f"{stem}.answer.json"
        assert prompt_path.exists() and schema_path.exists()

        # Over the schema's maxLength: 400.
        over_length = "Always mention the guest's first name. " * 12
        assert len(over_length) > 400
        answer_path.write_text(json.dumps({"suggestion": over_length}), encoding="utf-8")

        second = coach.run(settings, store, provider="interactive")
        assert second["suggestions"] == 0
        assert second["pending"] == []  # this cluster doesn't re-pend, it errors
        assert len(second["errors"]) == 1
        assert applied_to in second["errors"][0]
        assert "schema" in second["errors"][0].lower()
        assert coach.list_suggestions(store) == []  # the FALLBACK was never written

        # Cursor did not advance - re-offered next run, not skipped forever.
        assert store.get_cursor("coach:last_run") is None

        # Nothing was deleted or renamed: the operator can fix the answer in
        # place, exactly where core.llm._interactive left it.
        assert prompt_path.exists() and schema_path.exists() and answer_path.exists()
        assert not answer_path.with_suffix(".json.used").exists()

        # Fix the answer and confirm the normal path still works afterward.
        answer_path.write_text(
            '{"suggestion": "Always use the guest\'s first name in the salutation."}',
            encoding="utf-8")
        third = coach.run(settings, store, provider="interactive")
        assert third["errors"] == [] and third["pending"] == []
        assert third["suggestions"] == 1
        assert store.get_cursor("coach:last_run") is not None
    finally:
        for leftover in pending_dir.glob(f"{stem}.*"):
            leftover.unlink(missing_ok=True)
        store.close()

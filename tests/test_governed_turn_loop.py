"""Unit tests for the governed Turn host loop.

These guard the two parsing defects that a real end-to-end run exposed, both of
which silently turned a settleable Turn into an "unknown" stop:

* ``turn run-once --format json`` nests the Turn facts differently from the flat
  markdown summary;
* LoopX reports a *single* eligible Todo on the agent channel as
  ``"<todo_id>: <text>"`` and only fills the multi-action portfolio when it has
  alternatives.

No CLI, model or network is involved here.
"""

from __future__ import annotations

import pytest

from loopx.dsh_goal_mode import governed_turn_loop as loop


def test_single_eligible_todo_is_read_from_the_agent_channel() -> None:
    payload = {
        "should_run": True,
        "interaction_contract": {
            "agent_channel": {"primary_action": "todo_abc123: add src/hello.py"}
        },
        "action_portfolio": {"primary": None, "suggested_actions": None},
    }

    assert loop._eligible_todo_ids(payload) == ["todo_abc123"]


def test_multiple_candidates_keep_the_recommended_order() -> None:
    payload = {
        "action_portfolio": {
            "primary": {"todo_id": "todo_first"},
            "suggested_actions": [
                {"todo_id": "todo_first"},
                {"todo_id": "todo_second"},
            ],
        }
    }

    assert loop._eligible_todo_ids(payload) == ["todo_first", "todo_second"]


def test_no_candidates_reports_empty_rather_than_guessing() -> None:
    assert loop._eligible_todo_ids({"action_portfolio": {}}) == []


def test_turn_report_is_read_through_nesting() -> None:
    """The same facts arrive nested under `--format json`."""

    payload = {
        "ok": True,
        "report": {
            "turn": {
                "status": "committed",
                "result_kind": "validated_progress",
                "validation": {"status": "passed"},
                "host_invoked": False,
                "completed_phases": ["host_execute", "validation", "durable_writeback"],
            },
            "recovery_from": "durable_writeback",
        },
    }

    record = loop._parse_turn_report(payload)

    assert record.status == "committed"
    assert record.result_kind == "validated_progress"
    assert record.host_invoked is False
    assert record.phases == ["host_execute", "validation", "durable_writeback"]
    assert record.recovery_from == "durable_writeback"


def test_fence_detection_finds_the_marker_at_any_depth() -> None:
    payload = {"a": {"b": [{"recovery_from": "durable_writeback"}]}}

    assert loop._deep_contains(payload, "durable_writeback") is True
    assert loop._deep_contains(payload, "something_else") is False


def test_missing_final_status_is_not_reported_as_committed() -> None:
    record = loop._parse_turn_report({"error": "accountable refresh is blocked"})

    assert record.status == "unknown"
    assert record.stop_reason == "accountable refresh is blocked"


def test_runtime_root_falls_back_when_the_registry_is_unreadable(tmp_path) -> None:
    resolved = loop._runtime_root_for(tmp_path / "missing" / "registry.json", tmp_path)

    assert resolved == __import__("pathlib").Path.home() / ".loopx"


def test_runtime_root_prefers_the_declared_common_root(tmp_path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text('{"common_runtime_root": "%s"}' % (tmp_path / "runtime"))

    assert loop._runtime_root_for(registry, tmp_path) == tmp_path / "runtime"


def test_in_progress_key_is_none_without_a_turns_directory(tmp_path) -> None:
    assert (
        loop._in_progress_turn_key(
            goal_id="g", registry_path=None, project=tmp_path
        )
        is None
    )


def test_loop_error_is_raised_when_loopx_is_unavailable(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(loop.LoopError):
        loop._run_cli(["loopx", "--version"], cwd=tmp_path, timeout=5)


# --------------------------------------------------------------------------
# Continuation declaration (LoopX's autonomous_replan_required obligation)
# --------------------------------------------------------------------------


def test_single_blocked_todo_becomes_the_declared_successor(monkeypatch) -> None:
    monkeypatch.setattr(
        loop, "_blocked_todo_ids", lambda **_kwargs: ["todo_dependent"]
    )

    declaration = loop._successor_declaration(
        base=["loopx"], goal_id="g", project=__import__("pathlib").Path("."),
        timeout_seconds=5,
    )

    assert declaration == ["--successor-todo-id", "todo_dependent"]


def test_no_blocked_todo_declares_no_follow_up(monkeypatch) -> None:
    monkeypatch.setattr(loop, "_blocked_todo_ids", lambda **_kwargs: [])

    declaration = loop._successor_declaration(
        base=["loopx"], goal_id="g", project=__import__("pathlib").Path("."),
        timeout_seconds=5,
    )

    assert declaration == ["--no-follow-up"]


def test_ambiguous_successors_are_not_guessed(monkeypatch) -> None:
    """Two blocked candidates must keep LoopX's obligation, not pick one."""

    monkeypatch.setattr(
        loop, "_blocked_todo_ids", lambda **_kwargs: ["todo_a", "todo_b"]
    )

    declaration = loop._successor_declaration(
        base=["loopx"], goal_id="g", project=__import__("pathlib").Path("."),
        timeout_seconds=5,
    )

    assert declaration == []

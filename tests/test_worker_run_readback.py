"""Read-only run readback projection (FR-11 / AC-15).

The readback must let a Web read make execution traceable without ever becoming
evidence itself, and it must not leak local state. These tests pin both: the
facts travel, the private material does not.
"""

from __future__ import annotations

import json
from pathlib import Path

from loopx.control_plane import worker_run_readback as readback


def _journal(
    root: Path,
    *,
    goal: str,
    name: str,
    status: str = "committed",
    turn_instance_id: str = "turn-1",
    todo_id: str = "todo_a",
    validation: dict | None = None,
) -> Path:
    turns = root / "goals" / goal / "turns"
    turns.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "loopx_turn_journal_v0",
        "turn_key": f"sha256:{name}",
        "goal_id": goal,
        "status": status,
        "completed_phases": ["host_execute", "typed_result", "validation"],
        "host_attempt_count": 1,
        "plan": {"transaction": {"turn_instance_id": turn_instance_id}},
        "route": {"selected_todo": {"todo_id": todo_id}},
    }
    if validation is not None:
        payload["validation"] = validation
    path = turns / f"{name}.json"
    path.write_text(json.dumps(payload))
    return path


def test_projects_the_facts_ac15_needs(tmp_path: Path) -> None:
    _journal(
        tmp_path,
        goal="g",
        name="abc123def456",
        todo_id="todo_one",
        validation={"status": "passed", "exit_code": 0, "validator_kind": "command"},
    )

    history = readback.goal_turn_history(goal_id="g", runtime_root=tmp_path)

    row = history["runs"][0]
    assert history["turn_count"] == 1
    assert row["turn_label"] == "abc123def456"
    assert row["todo_id"] == "todo_one"
    assert row["status"] == "committed"
    assert row["validation"]["passed"] is True
    assert row["validation"]["exit_code"] == 0
    assert row["completed_phases"] == ["host_execute", "typed_result", "validation"]
    assert row["recorded_at"]
    assert row["recorded_at_source"] == "journal_file_mtime"


def test_missing_validation_is_unknown_not_passed(tmp_path: Path) -> None:
    _journal(tmp_path, goal="g", name="n1")

    row = readback.goal_turn_history(goal_id="g", runtime_root=tmp_path)["runs"][0]

    assert row["validation"]["status"] == "unknown"
    assert row["validation"]["passed"] is None


def test_a_failed_turn_projects_its_typed_failure(tmp_path: Path) -> None:
    path = _journal(tmp_path, goal="g", name="n2", status="failed")
    payload = json.loads(path.read_text())
    payload["result_kind"] = "host_failure"
    payload["host_failure"] = {"kind": "rate_limited", "attempt": 1, "retryable": True}
    path.write_text(json.dumps(payload))

    row = readback.goal_turn_history(goal_id="g", runtime_root=tmp_path)["runs"][0]

    assert row["status"] == "failed"
    assert row["result_kind"] == "host_failure"
    assert row["host_failure_kind"] == "rate_limited"


def test_projection_carries_no_local_state(tmp_path: Path) -> None:
    _journal(tmp_path, goal="g", name="n3")

    history = readback.goal_turn_history(goal_id="g", runtime_root=tmp_path)

    serialized = json.dumps(history)
    assert str(tmp_path) not in serialized
    assert "/home/" not in serialized
    assert "session-" not in serialized


def test_absent_journal_directory_is_an_empty_history(tmp_path: Path) -> None:
    history = readback.goal_turn_history(goal_id="never-ran", runtime_root=tmp_path)

    assert history["turn_count"] == 0
    assert history["runs"] == []


def test_history_is_bounded_and_newest_first(tmp_path: Path) -> None:
    import os
    import time

    for index in range(4):
        path = _journal(tmp_path, goal="g", name=f"n{index}")
        os.utime(path, (time.time() - 100 + index, time.time() - 100 + index))

    history = readback.goal_turn_history(goal_id="g", runtime_root=tmp_path, limit=2)

    assert history["turn_count"] == 4
    assert history["returned"] == 2
    assert history["runs"][0]["turn_label"] == "n3"


def test_limit_is_clamped(tmp_path: Path) -> None:
    _journal(tmp_path, goal="g", name="n1")

    assert readback.goal_turn_history(
        goal_id="g", runtime_root=tmp_path, limit=0
    )["returned"] == 1
    assert readback.goal_turn_history(
        goal_id="g", runtime_root=tmp_path, limit=10_000
    )["returned"] == 1


def test_unreadable_journal_is_skipped_not_fatal(tmp_path: Path) -> None:
    _journal(tmp_path, goal="g", name="good")
    (tmp_path / "goals" / "g" / "turns" / "broken.json").write_text("{not json")

    history = readback.goal_turn_history(goal_id="g", runtime_root=tmp_path)

    assert history["turn_count"] == 1

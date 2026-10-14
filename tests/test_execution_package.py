"""Unit tests for the frozen Execution Package builder and host request mirror."""

from __future__ import annotations

from loopx.control_plane.turn_driver.execution_package import (
    EXECUTION_PACKAGE_SCHEMA_VERSION,
    build_execution_observation,
    build_execution_package,
)
from loopx.control_plane.turn_driver.executor import build_loopx_turn_host_request
from loopx.control_plane.worker_run_readback import project_turn_journal


def _minimal_plan(**extra):
    turn_key = "sha256:" + "b" * 64
    plan = {
        "host": "dsh",
        "route": {"kind": "primary", "would_invoke_host": True},
        "session": {"context_policy": {"mode": "fresh"}},
        "transaction": {"turn_key": turn_key, "turn_instance_id": "turn-1"},
        "turn_envelope": {
            "goal_id": "goal-a",
            "agent_id": "agent-a",
            "action": {"selected_todo": {"todo_id": "todo_a"}},
        },
        "managed_executor": {
            "model": "deepseek-flash",
            "model_source": "goal_default",
            "provider": "deepseek-official",
            "reasoning_effort": "high",
        },
    }
    plan.update(extra)
    return plan


def test_build_execution_package_binds_identity_and_model() -> None:
    package = build_execution_package(
        _minimal_plan(),
        worker_endpoint_id="dsh-coding-worker-local",
        worker_kind="dsh_coding_worker",
        turn_host="dsh",
        model_route={
            "model": "deepseek-flash",
            "model_source": "goal_default",
            "provider": "deepseek-official",
            "reasoning_effort": "high",
        },
    )
    assert package["schema_version"] == EXECUTION_PACKAGE_SCHEMA_VERSION
    assert package["todo_id"] == "todo_a"
    assert package["model_applied"] == "deepseek-flash"
    assert package["model_source"] == "goal_default"


def test_host_request_mirrors_frozen_package() -> None:
    plan = _minimal_plan()
    plan["execution_package"] = build_execution_package(
        plan,
        turn_host="dsh",
        model_route=plan["managed_executor"],
    )
    request = build_loopx_turn_host_request(plan)
    assert request["execution_package"]["model_applied"] == "deepseek-flash"
    assert request["execution_package"] is not plan["execution_package"]


def test_readback_projects_model_segments_without_session() -> None:
    package = build_execution_package(
        _minimal_plan(),
        worker_kind="dsh_coding_worker",
        turn_host="dsh",
        model_route={"model": "m1", "model_source": "todo_override"},
    )
    observation = build_execution_observation(
        model_observed=None, model_observed_unknown_reason="not_reported_by_runtime"
    )
    row = project_turn_journal(
        {
            "schema_version": "loopx_turn_journal_v0",
            "turn_key": "sha256:" + "c" * 64,
            "status": "committed",
            "result_kind": "validated_progress",
            "execution_package": package,
            "execution_observation": observation,
            "completed_phases": [],
        }
    )
    assert row["runtime_kind"] == "dsh_coding_worker"
    assert row["model"]["requested"] == "m1"
    assert row["model"]["observed"] is None
    assert row["model"]["observed_unknown_reason"] == "not_reported_by_runtime"
    assert "session" not in row

"""Goal Closeout: CLI surface, creation policy and the terminal stop/resume rule.

These cases drive the real Python facade into the real TypeScript authority; the
acceptance basis is produced by the acceptance authority and its validator, never
by a hand-written pass flag.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from loopx.control_plane.goals.closeout import (
    GOAL_CLOSEOUT_COMPLETED_CLASSIFICATION,
    GOAL_CLOSEOUT_POLICY_STRICT_V1,
    confirm_goal_closeout,
    ensure_goal_closeout_policy,
    goal_closeout_completed,
    goal_closeout_completed_by_root,
    inspect_goal_closeout,
    prepare_goal_closeout,
    read_goal_closeout_history,
)
from loopx.cli_commands.turn_run_once import execute_turn_run_once
from loopx.control_plane.goals.acceptance import (
    configure_goal_acceptance,
    inspect_goal_acceptance,
    verify_goal_acceptance,
)
from loopx.control_plane.goals.activation_service import set_goal_activation_state
from loopx.control_plane.coordination.local_authority import LocalCoordinationAuthorityUnavailable
from loopx.control_plane.todos.provider_create import create_canonical_todo_if_promoted

from .canonical_authority_fixture import promoted_create_fixture

REPOSITORY = Path(__file__).resolve().parents[2]
GOAL_REF_INSTANCE = f"ginst_{'a' * 32}"


def _bind_goal_instance(registry_path: Path) -> None:
    """Give the fixture Goal a Goal Instance so completion can be exact."""
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["goals"][0]["goal_instance_id"] = GOAL_REF_INSTANCE
    registry_path.write_text(json.dumps(registry), encoding="utf-8")


def test_stamp_goal_closeout_policy_covers_every_goal_without_raising():
    from loopx.control_plane.goals.closeout import stamp_goal_closeout_policy

    payload = {"goals": [
        {"id": "a", "coordination": {"closeout_policy": "advisory"}},
        {"id": "b"},
        {"id": "c", "coordination": "not-an-object"},
        "not-a-goal",
    ]}
    stamp_goal_closeout_policy(payload)
    assert payload["goals"][0]["coordination"]["closeout_policy"] == GOAL_CLOSEOUT_POLICY_STRICT_V1
    assert payload["goals"][1]["coordination"]["closeout_policy"] == GOAL_CLOSEOUT_POLICY_STRICT_V1
    # A malformed record is skipped, never raised: this runs inside every
    # registry transaction.
    assert payload["goals"][2]["coordination"] == "not-an-object"


def test_completion_classification_cannot_flip_a_completed_goal_back():
    """AC-10: the completion run is status neutral, current risks still surface."""
    from loopx.history import STATUS_NEUTRAL_CLASSIFICATIONS, latest_status_run

    assert GOAL_CLOSEOUT_COMPLETED_CLASSIFICATION in STATUS_NEUTRAL_CLASSIFICATIONS
    closeout_run = {"run_id": "run-closeout", "status": "failed",
                    "classification": GOAL_CLOSEOUT_COMPLETED_CLASSIFICATION}
    done_run = {"run_id": "run-before", "status": "done",
                "classification": "execution_completed"}
    assert latest_status_run([closeout_run]) is None
    assert (latest_status_run([closeout_run, done_run]) or {}).get("run_id") == "run-before"
    # An unresolved current risk must still drive the displayed status.
    risk_run = {"run_id": "run-risk", "status": "failed",
                "classification": "run_index_collision"}
    assert (latest_status_run([risk_run, done_run]) or {}).get("run_id") == "run-risk"


def test_every_registration_entry_stamps_the_server_closeout_policy():
    entry = {"id": "goal-x", "coordination": {"registered_agents": ["agent-a"]}}
    ensure_goal_closeout_policy(entry)
    assert entry["coordination"]["closeout_policy"] == GOAL_CLOSEOUT_POLICY_STRICT_V1

    # A caller cannot downgrade or invent a policy.
    downgraded = {"coordination": {"closeout_policy": "advisory"}}
    ensure_goal_closeout_policy(downgraded)
    assert downgraded["coordination"]["closeout_policy"] == GOAL_CLOSEOUT_POLICY_STRICT_V1

    # bootstrap's Goal entry builder goes through the same stamping layer.
    from loopx.bootstrap import build_goal_entry

    built = build_goal_entry(
        project=Path("/tmp/project"),
        goal_id="goal-bootstrap",
        domain="project-goal-control-plane",
        role="controller",
        parent_goal_id=None,
        state_file=Path("/tmp/project/.loopx/goals/goal-bootstrap/ACTIVE_GOAL_STATE.md"),
        goal_doc=None,
        adapter_kind="generic_project_goal_v0",
        adapter_status="connected",
        next_probe=None,
        spawn_allowed=False,
        max_children=1,
        allowed_domains=[],
        write_scope=[],
        execution_profile=None,
    )
    assert built["coordination"]["closeout_policy"] == GOAL_CLOSEOUT_POLICY_STRICT_V1


def test_cli_exposes_the_four_closeout_actions():
    process = subprocess.run(
        [str(REPOSITORY / "scripts/loopx"), "goal-closeout", "--help"],
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert process.returncode == 0, process.stderr
    for action in ("inspect", "prepare", "confirm", "history"):
        assert action in process.stdout


def test_closeout_completed_is_false_without_a_canonical_authority(tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1, "goals": [
        {"id": "goal-missing", "repo": str(tmp_path), "state_file": "state.md"}]}), encoding="utf-8")
    assert goal_closeout_completed(registry_path=registry, goal_id="goal-missing") is False


@pytest.mark.parametrize("provider", ["file"])
def test_completed_goal_refuses_stop_and_resume(tmp_path, provider):
    registry_path, runtime_root, _state_file = promoted_create_fixture(tmp_path, provider=provider)
    _bind_goal_instance(registry_path)

    document = {
        "scope": {"kind": "all_advancement"},
        "objective": "Deliver independently validated work",
        "non_goals": ["Grant additional permissions"],
        "criteria": [{"id": "outcome", "description": "Outcome passes",
                      "validation_argv": ["node", "-e", "process.exit(0)"]}],
        "bindings": [],
    }
    configured = configure_goal_acceptance(
        registry_path=registry_path, goal_id="goal-a", runtime_root=str(runtime_root),
        document=document, expected_provider_revision=inspect_goal_acceptance(
            registry_path=registry_path, goal_id="goal-a", runtime_root=str(runtime_root)
        )["provider_revision"], execute=True,
    )
    assert configured["status"] == "applied", configured
    verified = verify_goal_acceptance(
        registry_path=registry_path, goal_id="goal-a", runtime_root=str(runtime_root), execute=True
    )
    assert verified["status"] == "applied", verified
    assert verified["acceptance_ready"] is True, verified

    # Nothing is completed yet, so the ordinary reversible pause still works.
    preview = set_goal_activation_state(
        registry_path=registry_path, goal_id="goal-a", state="stopped",
        runtime_root_override=str(runtime_root), execute=False,
    )
    assert preview is not None

    inspected = inspect_goal_closeout(
        registry_path=registry_path, goal_id="goal-a", runtime_root=str(runtime_root)
    )
    assert inspected["can_close"] is True, inspected
    prepared = prepare_goal_closeout(
        registry_path=registry_path, goal_id="goal-a", runtime_root=str(runtime_root),
        owner_principal_id="owner-1", execute=True,
    )
    assert prepared["status"] == "applied", prepared
    digest = prepared["basis_digest"]

    # An agent actor is refused before any write.
    with pytest.raises(ValueError, match="closeout_owner_required"):
        _agent_confirm(registry_path, runtime_root)

    confirmed = confirm_goal_closeout(
        registry_path=registry_path, goal_id="goal-a", runtime_root=str(runtime_root),
        owner_principal_id="owner-1", expected_basis_digest=digest, execute=True,
    )
    assert confirmed["status"] == "applied", confirmed
    assert confirmed["goal_closeout"]["completed"] is True

    history = read_goal_closeout_history(
        registry_path=registry_path, goal_id="goal-a", runtime_root=str(runtime_root)
    )
    assert history["status"] == "loaded"
    assert len(history["operations"]) == 2

    # AC-11: completed is terminal for the reversible lifecycle commands.
    assert goal_closeout_completed(
        registry_path=registry_path, goal_id="goal-a", runtime_root=str(runtime_root)
    ) is True
    for state in ("stopped", "active"):
        with pytest.raises(ValueError, match="goal_already_completed"):
            set_goal_activation_state(
                registry_path=registry_path, goal_id="goal-a", state=state,
                runtime_root_override=str(runtime_root), execute=False,
            )

    # AC-09: the canonical Todo write paths refuse a completed Goal, so an older
    # Host that predates Closeout still cannot reopen its Todo plan.
    with pytest.raises(LocalCoordinationAuthorityUnavailable, match="is completed"):
        create_canonical_todo_if_promoted(
            registry_path=registry_path, runtime_root=runtime_root, goal_id="goal-a",
            role="agent", text="must be refused", status="open", actor_agent_id="agent-a",
            claimed_by=None, metadata={}, dry_run=False, operation_id="ac09-create-attempt",
        )

    # AC-09: a completed Goal admits no Turn, checked from the runtime root alone.
    assert goal_closeout_completed_by_root(
        runtime_root=runtime_root, goal_id="goal-a"
    ) is True
    assert goal_closeout_completed_by_root(
        runtime_root=runtime_root, goal_id="goal-absent"
    ) is False
    turn = execute_turn_run_once(
        argparse.Namespace(), payload={}, registry_path=registry_path,
        runtime_root=runtime_root, runtime_root_arg=str(runtime_root),
        goal_ref={"goal_id": "goal-a"}, goal_admission=None, operator_environ={},
        operator_inbox_urgency_projector=lambda **kwargs: {},
    )
    assert "closeout_already_completed" in json.dumps(turn, default=str), turn


def _agent_confirm(registry_path: Path, runtime_root: Path) -> None:
    """The provider refuses an agent actor; the facade must surface that code."""
    from loopx.control_plane.effect_runtime import effect_runtime_result
    from loopx.control_plane.goals.closeout import CONFIRM_METHOD

    result = effect_runtime_result(CONFIRM_METHOD, {
        "runtime_root": str(runtime_root), "goal_id": "goal-a",
        "actor_agent_id": "agent-a", "owner_principal_id": "owner-1",
        "expected_basis_digest": "0" * 64, "dry_run": False,
        "operation_id": "closeout-agent-attempt",
    })
    assert result["status"] == "failed", result
    assert result["reason_code"] == "closeout_owner_required", result
    raise ValueError(str(result["reason_code"]))

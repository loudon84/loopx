"""Unit tests for Coding Worker model route resolution."""

from __future__ import annotations

import pytest

from loopx.control_plane.turn_driver import worker_route


def test_todo_override_beats_goal_and_profile() -> None:
    resolved = worker_route.resolve_worker_model_route(
        todo={"worker_model": "todo-model", "worker_reasoning_effort": "medium"},
        goal={"worker_model_default": "goal-model"},
        route_profile={"model_policy": {"model_id": "profile-model"}},
        environ={},
    )

    assert resolved["model"] == "todo-model"
    assert resolved["model_source"] == worker_route.PROFILE_SOURCE_TODO_OVERRIDE
    assert resolved["reasoning_effort"] == "medium"
    assert resolved["reasoning_effort_source"] == worker_route.PROFILE_SOURCE_TODO_OVERRIDE


def test_explicit_invalid_effort_is_rejected() -> None:
    with pytest.raises(worker_route.WorkerRouteError) as excinfo:
        worker_route.resolve_worker_model_route(
            explicit_reasoning_effort="turbo",
            allowed_efforts=("low", "medium", "high"),
            environ={},
        )

    assert excinfo.value.gate["kind"] == "worker_reasoning_effort_unsupported"


def test_model_selection_unsupported_fails_closed() -> None:
    with pytest.raises(worker_route.WorkerRouteError) as excinfo:
        worker_route.resolve_worker_model_route(
            explicit_model="any-model",
            model_selection_supported=False,
            environ={},
        )

    assert excinfo.value.gate["kind"] == "worker_model_selection_unsupported"


def test_goal_default_feeds_managed_profile_without_second_default_system() -> None:
    resolved = worker_route.resolve_worker_model_route(
        goal={"worker_model_default": "goal-flash"},
        environ={"LOOPX_TURN_MODEL": "env-model"},
    )

    assert resolved["model"] == "goal-flash"
    assert resolved["managed_profile"]["model"] == "goal-flash"
    assert resolved["model_source"] == worker_route.PROFILE_SOURCE_GOAL_DEFAULT


def test_attach_worker_route_stamps_plan_for_execution_package() -> None:
    plan: dict = {"host": "generic-cli"}
    route = worker_route.attach_worker_route_to_plan(
        plan,
        goal={
            "worker_model_default": "composer-1",
            "worker_endpoint_id": "cursor-cli-coding-worker-local",
        },
        environ={},
    )
    assert route["model"] == "composer-1"
    assert plan["worker_model_route"]["model"] == "composer-1"
    assert plan["worker_endpoint_id"] == "cursor-cli-coding-worker-local"

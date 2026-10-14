"""Coding Worker host-loop launch after Goal create."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from loopx.control_plane import worker_catalog
from loopx.dsh_goal_mode import host_loop_launch as launch


@pytest.fixture(autouse=True)
def _clear_active_loops() -> None:
    launch._ACTIVE.clear()


def test_launch_starts_background_host_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    seen: dict[str, object] = {}

    def _fake_run_host_loop(**kwargs: object) -> SimpleNamespace:
        seen.update(kwargs)
        started.set()
        return SimpleNamespace(
            as_dict=lambda: {"stop_kind": "no_work", "host_invocations": 0}
        )

    monkeypatch.setattr(launch, "run_host_loop", _fake_run_host_loop)
    endpoint = "cursor-cli-coding-worker-local"
    actor = worker_catalog.coordination_actor_id_for_endpoint(endpoint)
    handle = launch.launch_coding_worker_host_loop(
        goal_id="goal-cursor-1",
        actor_id=actor,
        project=tmp_path,
        worker_endpoint_id=endpoint,
        registry_path=tmp_path / "registry.json",
        max_turns=2,
    )
    assert started.wait(timeout=2.0)
    handle.thread.join(timeout=2.0)
    assert handle.as_dict()["alive"] is False
    assert handle.outcome == {"stop_kind": "no_work", "host_invocations": 0}
    assert seen["goal_id"] == "goal-cursor-1"
    assert seen["agent_id"] == actor
    assert seen["worker_endpoint_id"] == endpoint
    assert seen["dsh_runner"] is None


def test_launch_is_idempotent_while_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    entered = threading.Event()

    def _blocking(**_kwargs: object) -> SimpleNamespace:
        entered.set()
        release.wait(timeout=5.0)
        return SimpleNamespace(as_dict=lambda: {"stop_kind": "cancelled"})

    monkeypatch.setattr(launch, "run_host_loop", _blocking)
    endpoint = "dsh-coding-worker-local"
    actor = worker_catalog.coordination_actor_id_for_endpoint(endpoint)
    first = launch.launch_coding_worker_host_loop(
        goal_id="goal-dsh-1",
        actor_id=actor,
        project=tmp_path,
        worker_endpoint_id=endpoint,
    )
    assert entered.wait(timeout=2.0)
    second = launch.launch_coding_worker_host_loop(
        goal_id="goal-dsh-1",
        actor_id=actor,
        project=tmp_path,
        worker_endpoint_id=endpoint,
    )
    assert second is first
    assert first.as_dict()["alive"] is True
    release.set()
    first.thread.join(timeout=2.0)


def test_launch_rejects_unknown_endpoint(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown"):
        launch.launch_coding_worker_host_loop(
            goal_id="g",
            actor_id="coding-worker:x",
            project=tmp_path,
            worker_endpoint_id="not-a-worker",
        )

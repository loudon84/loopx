"""Launch the Coding Worker host loop after a Worker Goal is created.

``goal.create(executor_route=governed_turn_worker)`` must not stop at Todo
authoring. Chat Agent Goals open a first Turn; Coding Worker Goals start the
governed host loop that asks ``quota should-run`` and drives ``turn run-once``.
Without this launch, Todos stay open and the dashboard shows them as
unassigned with no executor ever claiming work.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loopx.control_plane import worker_catalog
from loopx.dsh_goal_mode.governed_turn_loop import LoopError, run_host_loop

_LOG = logging.getLogger(__name__)

_DEFAULT_DSH_RUNNER = Path(__file__).resolve().parent / "dsh_cli_runner.py"

# One live loop per Goal in this process. A second create/apply for the same
# Goal reuses the cancel event instead of stacking silent executors.
_ACTIVE: dict[str, "HostLoopHandle"] = {}
_LOCK = threading.Lock()


@dataclass
class HostLoopHandle:
    """In-process receipt for one background Coding Worker host loop."""

    goal_id: str
    actor_id: str
    worker_endpoint_id: str
    thread: threading.Thread
    cancel: threading.Event = field(default_factory=threading.Event)
    error: str | None = None
    outcome: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "actor_id": self.actor_id,
            "worker_endpoint_id": self.worker_endpoint_id,
            "alive": self.thread.is_alive(),
            "cancelled": self.cancel.is_set(),
            "error": self.error,
            "stop_kind": (
                None
                if self.outcome is None
                else self.outcome.get("stop_kind")
            ),
        }


def active_host_loop(goal_id: str) -> HostLoopHandle | None:
    with _LOCK:
        handle = _ACTIVE.get(str(goal_id))
        if handle is None:
            return None
        if handle.thread.is_alive():
            return handle
        return handle


def cancel_host_loop(goal_id: str) -> bool:
    """Signal a live host loop to stop before its next Turn."""

    with _LOCK:
        handle = _ACTIVE.get(str(goal_id))
    if handle is None or not handle.thread.is_alive():
        return False
    handle.cancel.set()
    return True


def repair_coding_worker_goal_binding(
    *,
    registry_path: Path,
    goal_id: str,
) -> dict[str, Any]:
    """Register the endpoint actor on an existing Coding Worker Goal.

    Goals created before actor binding left ``registered_agents`` empty, so Todo
    updates failed with ``requires a registered actor`` and the board stayed
    未分配. This repair is idempotent and does not start the host loop.
    """

    from loopx.configure_goal import configure_goal
    from loopx.history import load_registry
    from loopx.registry import registry_goals

    registry = load_registry(registry_path)
    goal = next(
        (
            item
            for item in registry_goals(registry)
            if str(item.get("id")) == str(goal_id)
        ),
        None,
    )
    if goal is None:
        raise ValueError(f"goal_id not found in registry: {goal_id}")
    if (
        str(goal.get("executor_route") or "")
        != worker_catalog.EXECUTOR_ROUTE_GOVERNED_TURN_WORKER
    ):
        raise ValueError(
            f"goal {goal_id!r} is not a governed_turn_worker Goal"
        )
    endpoint = str(goal.get("worker_endpoint_id") or "").strip()
    if not endpoint:
        raise ValueError(f"goal {goal_id!r} has no worker_endpoint_id")
    actor = worker_catalog.coordination_actor_id_for_endpoint(endpoint)
    configure_goal(
        registry_path=registry_path,
        goal_id=goal_id,
        registered_agents=[actor],
        execute=True,
    )
    return {
        "goal_id": goal_id,
        "worker_endpoint_id": endpoint,
        "actor_id": actor,
        "outcome": "worker_actor_bound",
    }


def launch_coding_worker_host_loop(
    *,
    goal_id: str,
    actor_id: str,
    project: Path,
    worker_endpoint_id: str,
    registry_path: Path | None = None,
    max_turns: int = 8,
    on_finished: Callable[[HostLoopHandle], None] | None = None,
) -> HostLoopHandle:
    """Start (or reuse) the background host loop for one Coding Worker Goal.

    Idempotent per Goal id inside this process: a live loop is returned as-is.
    Failures inside the thread are recorded on the handle; they do not raise
    into the Goal-create apply path after launch returns.
    """

    goal_key = str(goal_id).strip()
    actor = str(actor_id).strip()
    endpoint = str(worker_endpoint_id).strip()
    if not goal_key or not actor or not endpoint:
        raise ValueError("goal_id, actor_id, and worker_endpoint_id are required")
    if endpoint not in worker_catalog.BUILTIN_WORKER_ENDPOINTS:
        raise ValueError(f"unknown Coding Worker endpoint: {endpoint!r}")

    with _LOCK:
        existing = _ACTIVE.get(goal_key)
        if existing is not None and existing.thread.is_alive():
            return existing

        cancel = threading.Event()
        # Placeholder thread replaced immediately below so HostLoopHandle can
        # close over itself inside the worker target.
        handle = HostLoopHandle(
            goal_id=goal_key,
            actor_id=actor,
            worker_endpoint_id=endpoint,
            thread=threading.Thread(name=f"coding-worker:{goal_key}", daemon=True),
            cancel=cancel,
        )

        def _run() -> None:
            try:
                turn_host = str(
                    worker_catalog.BUILTIN_WORKER_ENDPOINTS[endpoint].get(
                        "turn_host"
                    )
                    or ""
                )
                dsh_runner = (
                    _DEFAULT_DSH_RUNNER
                    if turn_host == worker_catalog.TURN_HOST_DSH
                    else None
                )
                outcome = run_host_loop(
                    goal_id=goal_key,
                    agent_id=actor,
                    project=project.expanduser().resolve(),
                    dsh_runner=dsh_runner,
                    max_turns=max_turns,
                    registry_path=registry_path,
                    worker_endpoint_id=endpoint,
                    cancel=cancel,
                )
                handle.outcome = outcome.as_dict()
            except LoopError as exc:
                handle.error = str(exc)
                _LOG.warning(
                    "Coding Worker host loop stopped for goal %s: %s",
                    goal_key,
                    exc,
                )
            except Exception as exc:  # noqa: BLE001 - isolate background failures
                handle.error = str(exc)
                _LOG.exception(
                    "Coding Worker host loop crashed for goal %s", goal_key
                )
            finally:
                if on_finished is not None:
                    try:
                        on_finished(handle)
                    except Exception:  # noqa: BLE001
                        _LOG.exception(
                            "Coding Worker host loop finish hook failed for %s",
                            goal_key,
                        )

        handle.thread = threading.Thread(
            target=_run,
            name=f"coding-worker:{goal_key}",
            daemon=True,
        )
        _ACTIVE[goal_key] = handle
        handle.thread.start()
        return handle

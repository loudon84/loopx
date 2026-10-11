"""Host adapter for the canonical Goal Closeout transaction.

TypeScript owns the Closeout state machine, the basis digests, the CAS commit
and the owner gate. This module only resolves the registered Goal, routes the
call to the Effect Runtime, and exposes read-only helpers for the local gates.
No caller-supplied pass/fail, blocker list or completion flag is accepted here,
and no code path in this module writes a registry or a completion state
directly.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from ...agent_registry import load_goal_from_registry, registered_agent_ids_for_goal
from ...paths import effective_runtime_root
from ..coordination.local_authority import local_authority_is_promoted
from ..effect_runtime import (
    CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS,
    effect_runtime_result,
)
from .goal_ref_validation import exact_goal_ref

INSPECT_METHOD = "goal.closeout.inspect"
PREPARE_METHOD = "goal.closeout.prepare"
CONFIRM_METHOD = "goal.closeout.confirm"
HISTORY_METHOD = "goal.closeout.history"

#: Server-owned policy. A caller can never downgrade or override it.
GOAL_CLOSEOUT_POLICY_STRICT_V1 = "strict_v1"

_ACCEPTED_STATUSES = frozenset({"loaded", "applied", "recovered", "replayed", "planned", "no_change"})


def _routing(
    registry_path: Path,
    goal_id: str,
    runtime_root: str | None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    goal = load_goal_from_registry(registry_path, goal_id)
    if goal is None:
        raise ValueError("Goal closeout requires a registered Goal")
    if agent_id is not None and agent_id not in registered_agent_ids_for_goal(goal):
        raise ValueError("Goal closeout caller must be a registered Agent")
    root = effective_runtime_root(registry_path, runtime_root)
    if not local_authority_is_promoted(runtime_root=root, goal_id=goal_id):
        raise ValueError(
            "Goal closeout requires an existing canonical authority; activation never promotes a provider"
        )
    route: dict[str, Any] = {"runtime_root": str(root.resolve()), "goal_id": goal_id}
    goal_instance_id = goal.get("goal_instance_id")
    if goal_instance_id is not None:
        route["goal_ref"] = exact_goal_ref(goal_id, goal_instance_id)
    return route


def _result(method: str, request: Mapping[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if timeout is not None:
        kwargs["timeout"] = timeout
    value = effect_runtime_result(method, dict(request), **kwargs)
    if not isinstance(value, dict):
        raise TypeError("Goal closeout authority returned an invalid result")
    status = value.get("status")
    if status not in _ACCEPTED_STATUSES:
        raise ValueError(
            str(
                value.get("reason_code")
                or value.get("reason")
                or "Goal closeout authority is unavailable"
            )
        )
    return value


def inspect_goal_closeout(
    *,
    registry_path: Path,
    goal_id: str,
    runtime_root: str | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Read-only: blockers, basis and current Closeout state."""
    return _result(INSPECT_METHOD, _routing(registry_path, goal_id, runtime_root, agent_id))


def read_goal_closeout_history(
    *,
    registry_path: Path,
    goal_id: str,
    runtime_root: str | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Read-only audit: retained Closeout operations for this Goal."""
    return _result(HISTORY_METHOD, _routing(registry_path, goal_id, runtime_root, agent_id))


def prepare_goal_closeout(
    *,
    registry_path: Path,
    goal_id: str,
    runtime_root: str | None = None,
    agent_id: str | None = None,
    owner_principal_id: str | None = None,
    operation_id: str | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    """Derive every blocker on the current revision; only a clean basis prepares."""
    route = _routing(registry_path, goal_id, runtime_root, agent_id)
    request: dict[str, Any] = {
        **route,
        "actor_agent_id": agent_id,
        "owner_principal_id": owner_principal_id,
        "dry_run": not execute,
        "operation_id": operation_id or f"goal-closeout-prepare:{uuid4().hex}",
    }
    return _result(PREPARE_METHOD, request, timeout=CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS)


def confirm_goal_closeout(
    *,
    registry_path: Path,
    goal_id: str,
    expected_basis_digest: str,
    owner_principal_id: str,
    runtime_root: str | None = None,
    operation_id: str | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    """Owner-only terminal confirmation bound to the previewed basis digest.

    The CLI cannot authenticate an owner: this entrypoint records the principal
    the trusted host supplies and always refuses an agent actor. A browser owner
    session or a trusted OS/service policy remains the authentication authority.
    """
    route = _routing(registry_path, goal_id, runtime_root, None)
    request: dict[str, Any] = {
        **route,
        "actor_agent_id": None,
        "owner_principal_id": owner_principal_id,
        "expected_basis_digest": expected_basis_digest,
        "dry_run": not execute,
        "operation_id": operation_id or f"goal-closeout-confirm:{uuid4().hex}",
    }
    return _result(CONFIRM_METHOD, request, timeout=CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS)


def public_goal_closeout(value: Mapping[str, Any]) -> dict[str, Any]:
    """Expose projections and tokens only; never validator output or local paths."""
    keys = (
        "status",
        "goal_id",
        "provider_revision",
        "source_authority",
        "changed",
        "dry_run",
        "operation_id",
        "state",
        "can_close",
        "blockers",
        "required_todo_ids",
        "satisfied_todo_ids",
        "acceptance_status",
        "basis_digest",
        "goal_closeout",
        "reason_code",
        "reason",
    )
    return {"ok": value.get("status") not in {"failed"}, **{k: value[k] for k in keys if k in value}}


def goal_closeout_completed(
    *,
    registry_path: Path,
    goal_id: str,
    runtime_root: str | None = None,
) -> bool:
    """Read-only terminal check for the local gates.

    Returns False when no canonical authority exists yet: an unpromoted Goal
    cannot be completed, and this helper never promotes one.
    """
    try:
        inspected = inspect_goal_closeout(
            registry_path=registry_path, goal_id=goal_id, runtime_root=runtime_root
        )
    except (ValueError, TypeError, RuntimeError, OSError):
        # An unreadable or unpromoted authority is not a completed Closeout.
        # The terminal protection applies whenever the canonical read succeeds;
        # infrastructure failures must not block an unrelated reversible pause.
        return False
    projection = inspected.get("goal_closeout")
    if isinstance(projection, Mapping):
        return projection.get("completed") is True
    return inspected.get("state") == "completed"


def goal_closeout_completed_by_root(*, runtime_root: str | Path, goal_id: str) -> bool:
    """Terminal check when only the runtime root is known (no registry read).

    Used by the local gates (quota should-run, Turn driver) that already hold a
    goal id and a runtime root. Never promotes an authority and never writes.
    """
    if not goal_id:
        return False
    try:
        value = effect_runtime_result(INSPECT_METHOD, {
            "runtime_root": str(Path(runtime_root).resolve()), "goal_id": goal_id})
    except (ValueError, TypeError, RuntimeError, OSError):
        return False
    if not isinstance(value, dict) or value.get("status") in {None, "failed"}:
        return False
    projection = value.get("goal_closeout")
    if isinstance(projection, Mapping):
        return projection.get("completed") is True
    return value.get("state") == "completed"


def stamp_goal_closeout_policy(registry_payload: Mapping[str, Any]) -> None:
    """Force the server-owned Closeout policy on every Goal record in a registry.

    Called from the project-registry commit gateway, so bootstrap, `project
    register`, Web `goal.create`, configure-goal and migration all converge on
    the same policy without any of them having to remember to opt in.
    """
    goals = registry_payload.get("goals")
    if not isinstance(goals, list):
        return
    for goal in goals:
        if not isinstance(goal, dict):
            continue
        # A malformed coordination block is skipped rather than raised: this runs
        # inside every registry transaction, and stamping must never turn an
        # unrelated write into a failure.
        existing = goal.get("coordination")
        if existing is not None and not isinstance(existing, dict):
            continue
        ensure_goal_closeout_policy(goal)


def ensure_goal_closeout_policy(goal_entry: dict[str, Any]) -> dict[str, Any]:
    """Stamp the server-owned Closeout policy on a Goal record, in place.

    Every creation/registration entry point funnels through this helper, so a
    caller cannot create a Goal without the strict policy and cannot overwrite
    an existing one. Returns the entry for chaining.
    """
    coordination = goal_entry.setdefault("coordination", {})
    if not isinstance(coordination, dict):
        raise ValueError("Goal coordination block must be an object")
    # Always rewritten to the server value: a caller can neither downgrade nor
    # invent a policy, and a re-registration cannot drift away from strict_v1.
    coordination["closeout_policy"] = GOAL_CLOSEOUT_POLICY_STRICT_V1
    return goal_entry

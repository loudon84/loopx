"""Frozen Execution Package for one governed Turn.

The package is a nested journal/host-request object. It never enters the strict
host result field set. Once written, callers must not mutate it; retries reuse
the same snapshot and policy upgrades create a new Turn.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

EXECUTION_PACKAGE_SCHEMA_VERSION = "loopx_turn_execution_package_v0"
EXECUTION_OBSERVATION_SCHEMA_VERSION = "loopx_turn_execution_observation_v0"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def build_execution_package(
    plan: Mapping[str, Any],
    *,
    worker_endpoint_id: str | None = None,
    worker_kind: str | None = None,
    turn_host: str | None = None,
    adapter_version: str | None = None,
    cli_version: str | None = None,
    model_route: Mapping[str, Any] | None = None,
    session_policy: str | None = None,
    baseline_commit: str | None = None,
    workspace_dirty: bool | None = None,
) -> dict[str, Any]:
    """Build one immutable Execution Package from the Turn plan and route."""

    transaction = _mapping(plan.get("transaction"))
    envelope = _mapping(plan.get("turn_envelope"))
    action = _mapping(envelope.get("action"))
    selected = _mapping(action.get("selected_todo"))
    goal_ref = plan.get("goal_ref")
    route = model_route or {}
    managed = _mapping(route.get("managed_profile"))
    return {
        "schema_version": EXECUTION_PACKAGE_SCHEMA_VERSION,
        "goal_id": _text(envelope.get("goal_id") or plan.get("goal_id")),
        "goal_ref": dict(goal_ref) if isinstance(goal_ref, Mapping) else None,
        "todo_id": _text(selected.get("todo_id")),
        "turn_key": _text(transaction.get("turn_key")),
        "turn_instance_id": _text(
            transaction.get("turn_instance_id") or transaction.get("turn_key")
        ),
        "worker_endpoint_id": _text(worker_endpoint_id),
        "worker_kind": _text(worker_kind),
        "turn_host": _text(turn_host or plan.get("host")),
        "adapter_version": _text(adapter_version),
        "cli_version": _text(cli_version),
        "model_requested": _text(route.get("model") or managed.get("model")),
        "model_resolved": _text(route.get("model") or managed.get("model")),
        "model_applied": _text(route.get("model") or managed.get("model")),
        "model_source": _text(route.get("model_source") or managed.get("model_source")),
        "provider": _text(route.get("provider") or managed.get("provider")),
        "reasoning_effort": _text(
            route.get("reasoning_effort") or managed.get("reasoning_effort")
        ),
        "route_profile_id": _text(route.get("route_profile_id")),
        "route_profile_version": route.get("route_profile_version"),
        "session_policy": _text(session_policy) or "new_per_attempt",
        "baseline_commit": _text(baseline_commit),
        "workspace_dirty": workspace_dirty,
    }


def build_execution_observation(
    *,
    model_observed: str | None = None,
    model_observed_unknown_reason: str | None = None,
    usage: Mapping[str, Any] | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
    diff_file_count: int | None = None,
    diff_summary: str | None = None,
) -> dict[str, Any]:
    """Build post-settlement observation evidence (may be written after host)."""

    usage_payload = dict(usage) if isinstance(usage, Mapping) else {}
    return {
        "schema_version": EXECUTION_OBSERVATION_SCHEMA_VERSION,
        "model_observed": _text(model_observed),
        "model_observed_unknown_reason": (
            None
            if _text(model_observed)
            else (_text(model_observed_unknown_reason) or "not_reported_by_runtime")
        ),
        "usage": usage_payload,
        "started_at": _text(started_at),
        "ended_at": _text(ended_at),
        "diff_file_count": diff_file_count,
        "diff_summary": _text(diff_summary),
    }


def ensure_plan_execution_package(
    plan: dict[str, Any],
    *,
    turn_host: str | None = None,
    worker_endpoint_id: str | None = None,
    worker_kind: str | None = None,
) -> dict[str, Any]:
    """Ensure ``plan`` carries a frozen execution_package before host request.

    Idempotent: an existing package is returned unchanged so retries reuse the
    same snapshot. Callers must invoke this before
    :func:`build_loopx_turn_host_request` so Cursor/generic-cli adapters see the
    applied model.
    """

    existing = plan.get("execution_package")
    if isinstance(existing, Mapping):
        return dict(existing)

    route = plan.get("worker_model_route")
    if not isinstance(route, Mapping):
        managed = plan.get("managed_executor")
        route = managed if isinstance(managed, Mapping) else None
    host_value = turn_host
    if not host_value:
        planned_host = plan.get("host")
        if isinstance(planned_host, Mapping):
            host_value = _text(planned_host.get("kind"))
        else:
            host_value = _text(planned_host)
    package = build_execution_package(
        plan,
        worker_endpoint_id=worker_endpoint_id
        or _text(plan.get("worker_endpoint_id")),
        worker_kind=worker_kind or _text(plan.get("worker_kind")),
        turn_host=host_value,
        model_route=route,
    )
    plan["execution_package"] = package
    return package


def observation_from_host_result(
    result: Mapping[str, Any] | None,
    *,
    journal: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project observed model/usage from host output without inventing zeros."""

    payload = result if isinstance(result, Mapping) else {}
    host_result = (
        journal.get("host_result")
        if isinstance(journal, Mapping) and isinstance(journal.get("host_result"), Mapping)
        else {}
    )
    observed = (
        _text(payload.get("model_observed"))
        or _text(payload.get("observed_model"))
        or _text(host_result.get("model_observed"))
        or _text(host_result.get("observed_model"))
        or _text(_mapping(payload.get("receipt")).get("model"))
    )
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        usage = host_result.get("usage") if isinstance(host_result.get("usage"), Mapping) else None
    return build_execution_observation(
        model_observed=observed,
        usage=usage if isinstance(usage, Mapping) else None,
        started_at=_text(payload.get("started_at") or host_result.get("started_at")),
        ended_at=_text(payload.get("ended_at") or host_result.get("ended_at")),
        diff_file_count=(
            payload.get("diff_file_count")
            if isinstance(payload.get("diff_file_count"), int)
            else None
        ),
        diff_summary=_text(payload.get("diff_summary")),
    )

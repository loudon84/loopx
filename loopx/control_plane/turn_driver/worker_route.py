"""Resolve the Coding Worker model line for one governed Turn.

This module is the only place that turns Todo / Goal / route-profile preferences
into explicit arguments for :func:`managed_execution_profile`. It does not
invent a second default system: after choosing explicit fields it always hands
them to the existing managed profile owner, then records which product layer
supplied each explicit override.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .execution_profile import (
    PROFILE_SOURCE_EXPLICIT_ARGUMENT,
    managed_execution_profile,
    managed_profile_unavailable_reason,
)

WORKER_ROUTE_SCHEMA_VERSION = "loopx_worker_route_resolution_v0"

PROFILE_SOURCE_TODO_OVERRIDE = "todo_override"
PROFILE_SOURCE_GOAL_DEFAULT = "goal_default"
PROFILE_SOURCE_ROUTE_PROFILE = "route_profile"
PROFILE_SOURCE_ENDPOINT_DEFAULT = "endpoint_default"

ROUTE_EXPLICIT_SOURCES = frozenset(
    {
        PROFILE_SOURCE_TODO_OVERRIDE,
        PROFILE_SOURCE_GOAL_DEFAULT,
        PROFILE_SOURCE_ROUTE_PROFILE,
        PROFILE_SOURCE_ENDPOINT_DEFAULT,
        PROFILE_SOURCE_EXPLICIT_ARGUMENT,
    }
)


class WorkerRouteError(ValueError):
    """An explicit worker model preference cannot be applied."""

    def __init__(self, reason: str, *, gate: Mapping[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.gate = dict(gate) if gate else None


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _first_present(*candidates: tuple[str | None, str]) -> tuple[str | None, str | None]:
    for value, source in candidates:
        if value:
            return value, source
    return None, None


def resolve_worker_model_route(
    *,
    todo: Mapping[str, Any] | None = None,
    goal: Mapping[str, Any] | None = None,
    route_profile: Mapping[str, Any] | None = None,
    endpoint_defaults: Mapping[str, Any] | None = None,
    explicit_model: str | None = None,
    explicit_reasoning_effort: str | None = None,
    explicit_provider: str | None = None,
    environ: Mapping[str, str] | None = None,
    allowed_efforts: tuple[str, ...] | None = None,
    model_selection_supported: bool = True,
) -> dict[str, Any]:
    """Resolve one Turn's provider/model/effort with provenance.

    Precedence for each field independently:

    1. caller explicit CLI argument
    2. Todo override
    3. Goal default
    4. worker_route_profile
    5. endpoint default
    6. managed_execution_profile env / product default

    An explicit value that the endpoint cannot honour fails closed. Automatic
    fallback to another model is never invented here.
    """

    todo = todo or {}
    goal = goal or {}
    route_profile = route_profile or {}
    endpoint_defaults = endpoint_defaults or {}
    profile_model_policy = (
        route_profile.get("model_policy")
        if isinstance(route_profile.get("model_policy"), Mapping)
        else route_profile
    )

    model, model_route_source = _first_present(
        (_text(explicit_model), PROFILE_SOURCE_EXPLICIT_ARGUMENT),
        (_text(todo.get("worker_model")), PROFILE_SOURCE_TODO_OVERRIDE),
        (_text(goal.get("worker_model_default")), PROFILE_SOURCE_GOAL_DEFAULT),
        (_text(profile_model_policy.get("model_id") or profile_model_policy.get("model")), PROFILE_SOURCE_ROUTE_PROFILE),
        (_text(endpoint_defaults.get("model")), PROFILE_SOURCE_ENDPOINT_DEFAULT),
    )
    effort, effort_route_source = _first_present(
        (_text(explicit_reasoning_effort), PROFILE_SOURCE_EXPLICIT_ARGUMENT),
        (_text(todo.get("worker_reasoning_effort")), PROFILE_SOURCE_TODO_OVERRIDE),
        (_text(goal.get("worker_reasoning_effort_default")), PROFILE_SOURCE_GOAL_DEFAULT),
        (
            _text(
                profile_model_policy.get("reasoning_effort")
                or profile_model_policy.get("effort")
            ),
            PROFILE_SOURCE_ROUTE_PROFILE,
        ),
        (_text(endpoint_defaults.get("reasoning_effort")), PROFILE_SOURCE_ENDPOINT_DEFAULT),
    )
    provider, provider_route_source = _first_present(
        (_text(explicit_provider), PROFILE_SOURCE_EXPLICIT_ARGUMENT),
        (_text(todo.get("worker_provider")), PROFILE_SOURCE_TODO_OVERRIDE),
        (_text(goal.get("worker_provider_default")), PROFILE_SOURCE_GOAL_DEFAULT),
        (_text(profile_model_policy.get("provider")), PROFILE_SOURCE_ROUTE_PROFILE),
        (_text(endpoint_defaults.get("provider")), PROFILE_SOURCE_ENDPOINT_DEFAULT),
    )

    if model and not model_selection_supported:
        raise WorkerRouteError(
            "selected Coding Worker does not support per-Turn model selection",
            gate={
                "kind": "worker_model_selection_unsupported",
                "requested_model": model,
            },
        )

    if effort and allowed_efforts is not None and effort not in allowed_efforts:
        raise WorkerRouteError(
            f"reasoning effort {effort!r} is not supported by this worker endpoint",
            gate={
                "kind": "worker_reasoning_effort_unsupported",
                "requested_effort": effort,
                "allowed_efforts": list(allowed_efforts),
            },
        )

    profile = managed_execution_profile(
        environ,
        provider=provider,
        model=model,
        reasoning_effort=effort,
    )
    # Preserve managed profile sources for env/default fields; stamp the
    # product-layer source only when this resolver supplied the explicit value.
    if model and model_route_source:
        profile["model_source"] = model_route_source
        profile["model_env_var"] = ""
    if effort and effort_route_source:
        profile["reasoning_effort_source"] = effort_route_source
        profile["reasoning_effort_env_var"] = ""
    if provider and provider_route_source:
        profile["provider_source"] = provider_route_source
        profile["provider_env_var"] = ""

    unavailable = managed_profile_unavailable_reason(profile)
    if unavailable is not None:
        raise WorkerRouteError(
            f"resolved worker model profile is unavailable: {unavailable}",
            gate={"kind": "worker_model_profile_unavailable", "reason": unavailable},
        )

    return {
        "schema_version": WORKER_ROUTE_SCHEMA_VERSION,
        "provider": profile["provider"],
        "model": profile["model"],
        "reasoning_effort": profile["reasoning_effort"],
        "provider_source": profile["provider_source"],
        "model_source": profile["model_source"],
        "reasoning_effort_source": profile["reasoning_effort_source"],
        "managed_profile": profile,
        "route_profile_id": _text(route_profile.get("profile_id") or route_profile.get("id")),
        "route_profile_version": route_profile.get("profile_version")
        or route_profile.get("version"),
    }


def attach_worker_route_to_plan(
    plan: dict[str, Any],
    *,
    goal: Mapping[str, Any] | None,
    todo: Mapping[str, Any] | None = None,
    explicit_model: str | None = None,
    explicit_reasoning_effort: str | None = None,
    explicit_provider: str | None = None,
    environ: Mapping[str, str] | None = None,
    allowed_efforts: tuple[str, ...] | None = None,
    model_selection_supported: bool = True,
) -> dict[str, Any]:
    """Resolve the worker model route and stamp it onto ``plan``.

    The stamped ``worker_model_route`` is what
    :func:`ensure_plan_execution_package` freezes into the host request.
    """

    route_profile = None
    if isinstance(goal, Mapping):
        profile = goal.get("worker_route_profile")
        if isinstance(profile, Mapping):
            route_profile = profile
        elif profile is not None and not isinstance(profile, str):
            raise WorkerRouteError(
                "worker_route_profile must be a mapping when present on the Goal",
                gate={"kind": "worker_route_profile_invalid"},
            )
    route = resolve_worker_model_route(
        todo=todo,
        goal=goal,
        route_profile=route_profile,
        explicit_model=explicit_model,
        explicit_reasoning_effort=explicit_reasoning_effort,
        explicit_provider=explicit_provider,
        environ=environ,
        allowed_efforts=allowed_efforts,
        model_selection_supported=model_selection_supported,
    )
    plan["worker_model_route"] = route
    if goal and goal.get("worker_endpoint_id"):
        plan["worker_endpoint_id"] = str(goal.get("worker_endpoint_id"))
    if goal and goal.get("worker_kind"):
        plan["worker_kind"] = str(goal.get("worker_kind"))
    return route

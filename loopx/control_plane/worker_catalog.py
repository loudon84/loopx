"""Governed Turn Worker Catalog: the registration and admission surface for
execution-capable Coding Workers.

Why this module exists instead of extending the Chat endpoint registry
--------------------------------------------------------------------
``loopx/chat_endpoints.py`` is the *Chat* endpoint registry. It is deliberately
narrow: a custom endpoint must declare ``adapter_kind="acp"``,
``transport="stdio"`` and ``trust_scope="read_only"`` — validation rejects
anything else. A Coding Worker is none of those things. It is not an ACP
agent, and it must write inside an authorized workspace, so it cannot be
registered there without weakening the read-only guarantee that the managed
Steward Chat depends on.

The catalog is therefore a separate, additive read model with its own
admission rule. Nothing here enters, aliases, or relaxes the Chat endpoint
path, and the managed Steward endpoint keeps ``tool_calls=False`` /
``trust_scope="read_only"`` exactly as before.

Ownership
---------
This module owns *worker registration and admission only*. It does not own
Quota, Gates, Todo state, the Turn Journal, validation or writeback — those
stay with the existing governed-turn machinery. Registering a worker here
grants no work authority; it only answers "may this host execute a Turn right
now, and with which boundary?".

Execution authority is never inferred from natural language. The descriptor is
explicit configuration, and the capability verdict comes from a live probe.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..dsh_goal_mode.dsh_cli_probe import DshCliRuntime, probe_dsh_cli
from .worker_cli_probes import probe_codex_cli, probe_cursor_cli

WORKER_CATALOG_SCHEMA_VERSION = "loopx_governed_turn_worker_catalog_v1"
WORKER_DESCRIPTOR_SCHEMA_VERSION = "loopx_governed_turn_worker_descriptor_v1"

#: The two executor routes ``goal.create`` may select.
EXECUTOR_ROUTE_CHAT_AGENT = "chat_agent"
EXECUTOR_ROUTE_GOVERNED_TURN_WORKER = "governed_turn_worker"
EXECUTOR_ROUTES = frozenset(
    {EXECUTOR_ROUTE_CHAT_AGENT, EXECUTOR_ROUTE_GOVERNED_TURN_WORKER}
)

#: Opt-in switch for the DSH Coding Worker. Default OFF; kept for backward
#: compatibility with the original dark launch.
WORKER_FEATURE_FLAG_ENV = "LOOPX_DSH_CODING_WORKER"
CODEX_WORKER_FEATURE_FLAG_ENV = "LOOPX_CODEX_CODING_WORKER"
CURSOR_WORKER_FEATURE_FLAG_ENV = "LOOPX_CURSOR_CODING_WORKER"
_TRUTHY = {"1", "true", "yes", "y", "on"}

#: Turn ``run-once --host`` values a catalog endpoint may bind to.
TURN_HOST_DSH = "dsh"
TURN_HOST_CODEX_CLI = "codex-cli"
TURN_HOST_GENERIC_CLI = "generic-cli"
TURN_HOSTS = frozenset({TURN_HOST_DSH, TURN_HOST_CODEX_CLI, TURN_HOST_GENERIC_CLI})

#: Worker kinds this catalog can describe.
WORKER_KIND_DSH_CODING_WORKER = "dsh_coding_worker"
WORKER_KIND_CODEX_CLI_CODING_WORKER = "codex_cli_coding_worker"
WORKER_KIND_CURSOR_CLI_CODING_WORKER = "cursor_cli_coding_worker"
WORKER_KINDS = frozenset(
    {
        WORKER_KIND_DSH_CODING_WORKER,
        WORKER_KIND_CODEX_CLI_CODING_WORKER,
        WORKER_KIND_CURSOR_CLI_CODING_WORKER,
    }
)

#: The workspace-write trust scope a Coding Worker requires.
TRUST_SCOPE_WORKSPACE_WRITE = "workspace_write"

#: Declared control-plane capabilities for each builtin endpoint. These are
#: configuration claims; live probes may still mark measured fields separately.
_DEFAULT_CAPABILITIES: dict[str, Any] = {
    "model_selection": True,
    "reasoning_efforts": (),
    "session_resume": True,
    "permission_modes": (),
}

#: Built-in worker instances. Additive: the read-only Steward endpoint is not
#: listed here and is not affected by anything in this module.
BUILTIN_WORKER_ENDPOINTS: dict[str, dict[str, Any]] = {
    "dsh-coding-worker-local": {
        "display_name": "DSH Coding Worker",
        "worker_kind": WORKER_KIND_DSH_CODING_WORKER,
        "turn_host": TURN_HOST_DSH,
        "execution_program": "dsh_cli_headless",
        "execution_protocol": "loopx_turn_v0",
        "session_ownership": "managed_runtime",
        "trust_scope": TRUST_SCOPE_WORKSPACE_WRITE,
        "tool_calls": True,
        "command": ("dsh",),
        "session_mode": "fresh",
        "feature_flag": WORKER_FEATURE_FLAG_ENV,
        "capabilities": {
            **_DEFAULT_CAPABILITIES,
            "reasoning_efforts": ("low", "medium", "high", "max"),
            "permission_modes": ("workspace_write",),
        },
        "source": "builtin",
    },
    "codex-cli-coding-worker-local": {
        "display_name": "Codex CLI Coding Worker",
        "worker_kind": WORKER_KIND_CODEX_CLI_CODING_WORKER,
        "turn_host": TURN_HOST_CODEX_CLI,
        "execution_program": "codex_cli_headless",
        "execution_protocol": "loopx_turn_v0",
        "session_ownership": "managed_runtime",
        "trust_scope": TRUST_SCOPE_WORKSPACE_WRITE,
        "tool_calls": True,
        "command": ("codex",),
        "session_mode": "fresh",
        "feature_flag": CODEX_WORKER_FEATURE_FLAG_ENV,
        "capabilities": {
            **_DEFAULT_CAPABILITIES,
            "reasoning_efforts": ("low", "medium", "high", "xhigh"),
            "permission_modes": ("read-only", "workspace-write", "danger-full-access"),
        },
        "source": "builtin",
    },
    "cursor-cli-coding-worker-local": {
        "display_name": "Cursor CLI Coding Worker",
        "worker_kind": WORKER_KIND_CURSOR_CLI_CODING_WORKER,
        "turn_host": TURN_HOST_GENERIC_CLI,
        "execution_program": "cursor_cli_generic",
        "execution_protocol": "loopx_turn_v0",
        "session_ownership": "managed_runtime",
        "trust_scope": TRUST_SCOPE_WORKSPACE_WRITE,
        "tool_calls": True,
        # Prefer the current official binary name; fall back during probe.
        "command": ("agent", "cursor-agent"),
        "session_mode": "fresh",
        "feature_flag": CURSOR_WORKER_FEATURE_FLAG_ENV,
        "capabilities": {
            **_DEFAULT_CAPABILITIES,
            "reasoning_efforts": (),
            "permission_modes": ("default",),
        },
        "source": "builtin",
    },
}


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in _TRUTHY


def coding_worker_enabled(environment: Mapping[str, str] | None = None) -> bool:
    """Return whether the DSH Coding Worker capability is switched on.

    Default off, and only an explicit truthy value turns it on. Kept as the
    historical DSH flag helper; prefer :func:`endpoint_worker_enabled` for
    multi-runtime callers.
    """

    environ = environment if environment is not None else os.environ
    return _truthy(environ.get(WORKER_FEATURE_FLAG_ENV, ""))


def endpoint_feature_flag(endpoint_id: str) -> str | None:
    """Return the opt-in env var name for one builtin endpoint, if registered."""

    template = BUILTIN_WORKER_ENDPOINTS.get(str(endpoint_id or "").strip())
    if template is None:
        return None
    return str(template.get("feature_flag") or WORKER_FEATURE_FLAG_ENV)


def endpoint_worker_enabled(
    endpoint_id: str, *, environment: Mapping[str, str] | None = None
) -> bool:
    """Return whether one registered endpoint's own feature flag is on."""

    flag = endpoint_feature_flag(endpoint_id)
    if flag is None:
        return False
    environ = environment if environment is not None else os.environ
    return _truthy(environ.get(flag, ""))


def worker_unavailable_reason(
    endpoint_id: str, *, environment: Mapping[str, str] | None = None
) -> str:
    """Explain why a worker endpoint cannot be used, without guessing."""

    if str(endpoint_id or "").strip() not in BUILTIN_WORKER_ENDPOINTS:
        return "not_registered"
    if not endpoint_worker_enabled(endpoint_id, environment=environment):
        return "feature_flag_disabled"
    return "registered"


@dataclass(frozen=True)
class WorkerDescriptor:
    """One registered governed-turn worker instance.

    ``authorized_workspaces`` is the set of workspace roots the worker may write
    inside. It is explicit configuration, never parsed from the goal text or
    inferred from a Todo.
    """

    worker_endpoint_id: str
    display_name: str
    worker_kind: str
    turn_host: str
    execution_program: str
    execution_protocol: str
    session_ownership: str
    trust_scope: str
    tool_calls: bool
    command: tuple[str, ...]
    session_mode: str = "fresh"
    dsh_home: Path | None = None
    authorized_workspaces: tuple[Path, ...] = ()
    model: str | None = None
    reasoning_effort: str | None = None
    max_tokens: int | None = None
    timeout_seconds: float | None = None
    capabilities: Mapping[str, Any] = field(default_factory=dict)
    feature_flag: str = WORKER_FEATURE_FLAG_ENV
    source: str = "builtin"
    env: Mapping[str, str] = field(default_factory=dict)
    _extra_probe_env: Mapping[str, str] = field(default_factory=dict)

    def resolution_key(self) -> str:
        return f"{self.worker_kind}:{self.worker_endpoint_id}"

    def authorizes(self, workspace: Path) -> bool:
        """Return whether ``workspace`` is inside a declared writable root."""

        if not self.authorized_workspaces:
            return False
        selected = workspace.expanduser().resolve()
        for root in self.authorized_workspaces:
            try:
                selected.relative_to(root.expanduser().resolve())
            except ValueError:
                continue
            return True
        return False

    def runtime(self, *, workspace: Path) -> DshCliRuntime:
        """Build the DSH probe/runner runtime for this descriptor.

        Non-DSH descriptors must not call this; use :func:`probe_worker` instead.
        """

        if self.worker_kind != WORKER_KIND_DSH_CODING_WORKER:
            raise TypeError(
                f"runtime() is DSH-only; got worker_kind={self.worker_kind!r}"
            )
        return DshCliRuntime(
            dsh_bin=self.command[0] if self.command else "dsh",
            dsh_home=self.dsh_home,
            workspace=workspace,
            provider=None,
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            max_tokens=self.max_tokens,
            timeout_seconds=self.timeout_seconds,
            extra_env=dict(self.env) | dict(self._extra_probe_env),
        )


def _workspace_roots(raw: Any) -> tuple[Path, ...]:
    if isinstance(raw, (str, Path)):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return ()
    roots: list[Path] = []
    for item in raw:
        text = str(item or "").strip()
        if text:
            roots.append(Path(text).expanduser().resolve())
    return tuple(roots)


def resolve_worker(
    endpoint_id: str,
    *,
    environment: Mapping[str, str] | None = None,
    authorized_workspaces: Any = (),
    dsh_home: Path | str | None = None,
    session_mode: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    max_tokens: int | None = None,
    timeout_seconds: float | None = None,
    require_enabled: bool = True,
) -> WorkerDescriptor | None:
    """Resolve one worker endpoint id, or ``None`` when it is unavailable.

    With ``require_enabled`` (the default) a disabled feature flag resolves to
    ``None``, so no caller can admit or launch a worker that the machine has not
    opted into.

    Explicit configuration always outranks catalog defaults. ``environment``
    is consulted for ``DSH_HOME`` and binary overrides so the same resolution
    is used by admission, probe and launch.
    """

    template = BUILTIN_WORKER_ENDPOINTS.get(str(endpoint_id or "").strip())
    if template is None:
        return None

    environ = dict(environment if environment is not None else os.environ)
    if require_enabled and not endpoint_worker_enabled(
        str(endpoint_id).strip(), environment=environ
    ):
        return None
    env_dsh_home = str(environ.get("DSH_HOME") or "").strip()
    resolved_home = (
        Path(dsh_home).expanduser()
        if dsh_home is not None
        else Path(env_dsh_home).expanduser()
        if env_dsh_home
        else None
    )
    command = tuple(str(part) for part in template["command"])
    kind = str(template["worker_kind"])
    if kind == WORKER_KIND_DSH_CODING_WORKER:
        override_bin = str(environ.get("LOOPX_DSH_CODING_WORKER_BIN") or "").strip()
        if override_bin:
            command = (override_bin,)
    elif kind == WORKER_KIND_CODEX_CLI_CODING_WORKER:
        override_bin = str(environ.get("LOOPX_CODEX_CODING_WORKER_BIN") or "").strip()
        if override_bin:
            command = (override_bin,)
    elif kind == WORKER_KIND_CURSOR_CLI_CODING_WORKER:
        override_bin = str(environ.get("LOOPX_CURSOR_CODING_WORKER_BIN") or "").strip()
        if override_bin:
            command = (override_bin,)

    turn_host = str(template["turn_host"])
    if turn_host not in TURN_HOSTS:
        raise ValueError(f"unsupported turn_host in catalog: {turn_host!r}")

    return WorkerDescriptor(
        worker_endpoint_id=str(endpoint_id).strip(),
        display_name=str(template["display_name"]),
        worker_kind=kind,
        turn_host=turn_host,
        execution_program=str(template["execution_program"]),
        execution_protocol=str(template["execution_protocol"]),
        session_ownership=str(template["session_ownership"]),
        trust_scope=str(template["trust_scope"]),
        tool_calls=bool(template["tool_calls"]),
        command=command,
        session_mode=str(session_mode or template["session_mode"]),
        dsh_home=resolved_home,
        authorized_workspaces=_workspace_roots(authorized_workspaces),
        model=model,
        reasoning_effort=reasoning_effort,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        capabilities=dict(template.get("capabilities") or {}),
        feature_flag=str(template.get("feature_flag") or WORKER_FEATURE_FLAG_ENV),
        source=str(template["source"]),
    )


class WorkerNotAdmissible(ValueError):
    """A governed-turn worker cannot execute this Goal right now.

    The message is public-safe and names the failing condition, so the Web can
    show a concrete reason instead of a generic refusal.
    """

    def __init__(self, reason: str, *, gate: Mapping[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.gate = dict(gate) if gate else None


def probe_worker(
    descriptor: WorkerDescriptor, *, workspace: Path
) -> dict[str, Any]:
    """Run the kind-specific readiness probe for one worker descriptor."""

    resolved_workspace = workspace.expanduser().resolve()
    if descriptor.worker_kind == WORKER_KIND_DSH_CODING_WORKER:
        return probe_dsh_cli(descriptor.runtime(workspace=resolved_workspace))
    if descriptor.worker_kind == WORKER_KIND_CODEX_CLI_CODING_WORKER:
        return probe_codex_cli(
            command=descriptor.command,
            workspace=resolved_workspace,
            worker_endpoint_id=descriptor.worker_endpoint_id,
            environment=dict(os.environ) | dict(descriptor.env),
        )
    if descriptor.worker_kind == WORKER_KIND_CURSOR_CLI_CODING_WORKER:
        return probe_cursor_cli(
            command=descriptor.command,
            workspace=resolved_workspace,
            worker_endpoint_id=descriptor.worker_endpoint_id,
            environment=dict(os.environ) | dict(descriptor.env),
        )
    raise WorkerNotAdmissible(
        f"selected Coding Worker kind is not supported: {descriptor.worker_kind}",
        gate={
            "kind": "worker_kind_unsupported",
            "worker_endpoint_id": descriptor.worker_endpoint_id,
            "worker_kind": descriptor.worker_kind,
        },
    )


def admit_worker(
    descriptor: WorkerDescriptor | None,
    *,
    workspace: Path,
    endpoint_id: str,
) -> dict[str, Any]:
    """Admit or refuse one Coding Worker for one Goal workspace.

    Fails closed on: unregistered endpoint, healthy-but-unprobed execution,
    workspace outside the authorized roots, and an incompatible trust scope.
    This deliberately does **not** consult the Chat endpoint registry — a
    Coding Worker is not a Chat agent and must never be admitted by relaxing
    the Chat tool-check.
    """

    if descriptor is None:
        raise WorkerNotAdmissible(
            "selected Coding Worker endpoint is not registered",
            gate={"kind": "worker_endpoint_unknown", "worker_endpoint_id": endpoint_id},
        )
    if descriptor.trust_scope != TRUST_SCOPE_WORKSPACE_WRITE:
        raise WorkerNotAdmissible(
            "selected Coding Worker does not declare a workspace-write trust scope",
            gate={
                "kind": "worker_trust_scope_incompatible",
                "worker_endpoint_id": endpoint_id,
                "trust_scope": descriptor.trust_scope,
            },
        )
    if not descriptor.tool_calls:
        raise WorkerNotAdmissible(
            "selected Coding Worker declares no tool capability",
            gate={
                "kind": "worker_tool_capability_missing",
                "worker_endpoint_id": endpoint_id,
            },
        )

    resolved_workspace = workspace.expanduser().resolve()
    if not descriptor.authorizes(resolved_workspace):
        raise WorkerNotAdmissible(
            "selected Coding Worker is not authorized for this workspace",
            gate={
                "kind": "worker_workspace_not_authorized",
                "worker_endpoint_id": endpoint_id,
                "workspace_authorized": False,
            },
        )

    probe = probe_worker(descriptor, workspace=resolved_workspace)
    if probe.get("ready") is not True:
        raise WorkerNotAdmissible(
            f"selected Coding Worker is not available: {probe.get('reason')}",
            gate={
                "kind": "worker_not_ready",
                "worker_endpoint_id": endpoint_id,
                "ready": False,
                "reason": probe.get("reason"),
            },
        )
    return probe


def declaration_snapshot(
    descriptor: WorkerDescriptor, *, workspace: Path
) -> dict[str, Any]:
    """Describe a worker without spawning anything.

    The capabilities read is on the Web's hot path, and a live probe launches
    the host binary. This projection therefore reports only what the
    declaration and the workspace boundary already prove, and states
    ``not_probed`` openly instead of implying health. The authoritative
    readiness verdict is produced by :func:`admit_worker` at Goal creation,
    where a wrong answer would actually matter.
    """

    resolved_workspace = workspace.expanduser().resolve()
    declared = dict(descriptor.capabilities)
    return {
        "schema_version": WORKER_DESCRIPTOR_SCHEMA_VERSION,
        "worker_endpoint_id": descriptor.worker_endpoint_id,
        "display_name": descriptor.display_name,
        "worker_kind": descriptor.worker_kind,
        "turn_host": descriptor.turn_host,
        "execution_program": descriptor.execution_program,
        "execution_protocol": descriptor.execution_protocol,
        "session_ownership": descriptor.session_ownership,
        "session_mode": descriptor.session_mode,
        "trust_scope": descriptor.trust_scope,
        "tool_calls": descriptor.tool_calls,
        "source": descriptor.source,
        "feature_flag": descriptor.feature_flag,
        "workspace_authorized": descriptor.authorizes(resolved_workspace),
        "declared_capabilities": declared,
        "capabilities": {
            # Declared, not measured: never upgraded to True from configuration.
            "shell": "probe_required",
            "file_read": "probe_required",
            "workspace_write": "probe_required",
            "git": "probe_required",
            "session_resume": "probe_required",
            "structured_events": "probe_required",
            "model_selection": declared.get("model_selection", "probe_required"),
        },
        "availability": {"ready": False, "reason": "not_probed"},
        "failed_checks": [],
    }


def public_worker_snapshot(
    descriptor: WorkerDescriptor,
    *,
    workspace: Path,
    probe: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the redacted read-only projection the Web may display.

    Local absolute paths, the resolved binary path and the DSH home are never
    projected: the readback describes *whether* the worker can run and *what it
    may do*, not where the host keeps its private state.
    """

    resolved_workspace = workspace.expanduser().resolve()
    result = (
        probe
        if probe is not None
        else probe_worker(descriptor, workspace=resolved_workspace)
    )
    capability = dict(result.get("capability") or {})
    capabilities = dict(capability.get("capabilities") or {})
    return {
        "schema_version": WORKER_DESCRIPTOR_SCHEMA_VERSION,
        "worker_endpoint_id": descriptor.worker_endpoint_id,
        "display_name": descriptor.display_name,
        "worker_kind": descriptor.worker_kind,
        "turn_host": descriptor.turn_host,
        "execution_program": descriptor.execution_program,
        "execution_protocol": descriptor.execution_protocol,
        "session_ownership": descriptor.session_ownership,
        "session_mode": descriptor.session_mode,
        "trust_scope": descriptor.trust_scope,
        "tool_calls": descriptor.tool_calls,
        "source": descriptor.source,
        "feature_flag": descriptor.feature_flag,
        "workspace_authorized": descriptor.authorizes(resolved_workspace),
        "declared_capabilities": dict(descriptor.capabilities),
        "capabilities": capabilities,
        "availability": {
            "ready": bool(result.get("ready")),
            "reason": str(result.get("reason") or ""),
        },
        # Only the failing check *names* travel; their detail strings may
        # contain local paths and are intentionally omitted.
        "failed_checks": [
            check.get("name")
            for check in result.get("checks") or []
            if isinstance(check, Mapping) and check.get("required") and not check.get("ok")
        ],
    }


def worker_catalog_projection(
    *,
    workspace: Path | str,
    environment: Mapping[str, str] | None = None,
    authorized_workspaces: Any = None,
    dsh_home: Path | str | None = None,
    session_mode: str | None = None,
    probe_workers: bool = False,
) -> dict[str, Any]:
    """Return the read-only catalog projection for the Web capabilities read.

    ``probe_workers=False`` (the default for the HTTP read) describes the
    declared boundary without launching the host. ``probe_workers=True`` adds
    the live availability verdict, which is what admission uses.

    Probing is best-effort: an unregistered or unprobeable worker is reported as
    unavailable with a typed reason rather than raising, because this is a
    display read and must not break the capabilities endpoint.
    """

    resolved_workspace = Path(workspace).expanduser().resolve()
    roots = (
        authorized_workspaces
        if authorized_workspaces is not None
        else [resolved_workspace]
    )
    environ = dict(environment if environment is not None else os.environ)
    workers: list[dict[str, Any]] = []
    for endpoint_id in sorted(BUILTIN_WORKER_ENDPOINTS):
        descriptor = resolve_worker(
            endpoint_id,
            environment=environ,
            authorized_workspaces=roots,
            dsh_home=dsh_home,
            session_mode=session_mode,
        )
        if descriptor is None:
            continue
        if not probe_workers:
            workers.append(declaration_snapshot(descriptor, workspace=resolved_workspace))
            continue
        try:
            workers.append(
                public_worker_snapshot(descriptor, workspace=resolved_workspace)
            )
        except Exception:  # noqa: BLE001 - a display read must not break the endpoint
            workers.append(
                {
                    "schema_version": WORKER_DESCRIPTOR_SCHEMA_VERSION,
                    "worker_endpoint_id": descriptor.worker_endpoint_id,
                    "display_name": descriptor.display_name,
                    "worker_kind": descriptor.worker_kind,
                    "turn_host": descriptor.turn_host,
                    "trust_scope": descriptor.trust_scope,
                    "tool_calls": descriptor.tool_calls,
                    "workspace_authorized": descriptor.authorizes(resolved_workspace),
                    "capabilities": {},
                    "availability": {"ready": False, "reason": "probe_failed"},
                    "failed_checks": ["probe"],
                }
            )
    return {
        "schema_version": WORKER_CATALOG_SCHEMA_VERSION,
        "feature_flags": {
            WORKER_FEATURE_FLAG_ENV: {
                "enabled": coding_worker_enabled(environ),
                "default": "disabled",
            },
            CODEX_WORKER_FEATURE_FLAG_ENV: {
                "enabled": _truthy(environ.get(CODEX_WORKER_FEATURE_FLAG_ENV, "")),
                "default": "disabled",
            },
            CURSOR_WORKER_FEATURE_FLAG_ENV: {
                "enabled": _truthy(environ.get(CURSOR_WORKER_FEATURE_FLAG_ENV, "")),
                "default": "disabled",
            },
        },
        # Backward-compatible single-flag summary (DSH). Prefer feature_flags.
        "feature_flag": {
            "name": WORKER_FEATURE_FLAG_ENV,
            "enabled": coding_worker_enabled(environ),
            "default": "disabled",
        },
        "workers": workers,
    }


def normalize_executor_route(value: Any) -> str:
    """Validate one ``executor_route`` selection. Unknown values are rejected."""

    route = str(value or "").strip() or EXECUTOR_ROUTE_CHAT_AGENT
    if route not in EXECUTOR_ROUTES:
        raise ValueError(f"unsupported executor_route: {route!r}")
    return route

"""Governed Turn host loop for Coding Workers.

What this is
------------
LoopX owns Goal/Todo identity, eligibility, quota, validation and writeback. It
deliberately does *not* own a timer: for a generic CLI host it expects the host
to provide the loop that asks "should I run?" and then obeys the answer. This
module is that loop for registered Coding Workers (DSH, Codex CLI, Cursor CLI).

What this is not
----------------
It is not a scheduler. It never decides which Todo is eligible, never writes
LoopX state directly, never spends quota on its own judgement, and never
retries a Turn that produced unknown side effects. Every step is a call to the
authoritative ``loopx`` CLI, and the loop stops the moment LoopX says stop.

The automatic-continuation contract
-----------------------------------
One loop invocation is what makes the second Turn happen without a human
message: after a Turn settles, the loop asks ``quota should-run`` again and
LoopX either admits the next eligible Todo or refuses. That refusal — a user
gate, an exhausted budget, a pause, or simply no work left — ends the loop with
a typed reason rather than a guess.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loopx.control_plane import worker_catalog

LOOP_SCHEMA_VERSION = "loopx_dsh_coding_worker_loop_v1"
DEFAULT_MAX_TURNS = 8
DEFAULT_TIMEOUT_SECONDS = 1_800
DEFAULT_WORKER_ENDPOINT_ID = "dsh-coding-worker-local"


class LoopError(RuntimeError):
    """The loop could not ask LoopX, so it must not act."""


@dataclass
class TurnRecord:
    """Public-safe receipt for one Turn the loop drove."""

    turn_instance_id: str
    todo_id: str
    action: str
    status: str
    result_kind: str
    validation: str
    host_invoked: bool
    phases: list[str] = field(default_factory=list)
    recovery_from: str | None = None
    stop_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "turn_instance_id": self.turn_instance_id,
            "todo_id": self.todo_id,
            "action": self.action,
            "status": self.status,
            "result_kind": self.result_kind,
            "validation": self.validation,
            "host_invoked": self.host_invoked,
            "phases": list(self.phases),
            "recovery_from": self.recovery_from,
            "stop_reason": self.stop_reason,
        }


def _loopx_bin() -> str:
    return shutil.which("loopx") or "loopx"


def _goal_from_registry(
    *,
    goal_id: str,
    registry_path: Path | None,
) -> dict[str, Any] | None:
    """Load one Goal row from the registry without inventing defaults."""

    if registry_path is None or not registry_path.is_file():
        return None
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for goal in payload.get("goals") or []:
        if not isinstance(goal, Mapping):
            continue
        if str(goal.get("id") or "") != goal_id:
            continue
        return dict(goal)
    return None


def _goal_worker_endpoint_id(
    *,
    goal_id: str,
    registry_path: Path | None,
    explicit: str | None,
) -> str:
    """Resolve the worker endpoint for this Goal.

    Explicit CLI/API selection wins. Otherwise the Goal registry value is used.
    Missing configuration keeps the historical DSH default so existing callers
    that only pass ``--dsh-runner`` keep working.
    """

    if explicit and str(explicit).strip():
        return str(explicit).strip()
    goal = _goal_from_registry(goal_id=goal_id, registry_path=registry_path)
    if goal is not None:
        endpoint = str(goal.get("worker_endpoint_id") or "").strip()
        return endpoint or DEFAULT_WORKER_ENDPOINT_ID
    return DEFAULT_WORKER_ENDPOINT_ID


def _resolve_host_loop_actor_id(
    *,
    goal_id: str,
    registry_path: Path | None,
    explicit_agent_id: str,
    endpoint_id: str,
) -> str:
    """Resolve the Todo/Turn actor for one Coding Worker host loop.

    Steward Chat ids (``dsh`` / ``codex``) must not silently drive a Worker
    Goal. When the Goal roster already carries the endpoint's coordination
    actor, that actor wins over a mismatched CLI ``--agent-id``.
    """

    from loopx.agent_registry import registered_agent_ids_for_goal

    expected = worker_catalog.coordination_actor_id_for_endpoint(endpoint_id)
    explicit = str(explicit_agent_id or "").strip()
    goal = _goal_from_registry(goal_id=goal_id, registry_path=registry_path)
    registered = registered_agent_ids_for_goal(goal)
    if expected in registered:
        if explicit and explicit != expected and explicit in registered:
            raise LoopError(
                f"Coding Worker Goal {goal_id!r} is bound to actor {expected!r}; "
                f"refusing mismatched agent_id {explicit!r}"
            )
        return expected
    if registered:
        if explicit in registered:
            return explicit
        raise LoopError(
            f"agent_id {explicit!r} is not registered on Goal {goal_id!r}; "
            f"registered actors: {', '.join(registered)}"
        )
    # Pre-binding Goals (or callers that only supply --agent-id): prefer the
    # endpoint's stable actor so Todo updates do not fall through to a steward.
    return expected if not explicit else explicit


def build_turn_run_once_argv(
    *,
    base: Sequence[str],
    goal_id: str,
    agent_id: str,
    project: Path,
    todo_id: str,
    turn_instance_id: str,
    timeout_seconds: float,
    descriptor: worker_catalog.WorkerDescriptor,
    dsh_runner: Path | None,
    dsh_home: Path | None = None,
    codex_bin: str | None = None,
    cursor_adapter_command: Sequence[str] | None = None,
    validation_argv: list[str] | None = None,
) -> list[str]:
    """Build one ``turn run-once`` argv for the selected worker endpoint.

    The DSH branch is byte-for-byte compatible with the historical hard-coded
    argv (same flags, same order) so existing DSH loops keep their regression
    baseline.
    """

    turn_argv = list(base) + [
        "turn",
        "run-once",
        "--goal-id",
        goal_id,
        "--agent-id",
        agent_id,
        "--host",
        descriptor.turn_host,
        "--project",
        str(project),
        "--todo-id",
        todo_id,
        "--turn-instance-id",
        turn_instance_id,
    ]
    if descriptor.turn_host == worker_catalog.TURN_HOST_DSH:
        if dsh_runner is None:
            raise LoopError(
                "DSH Coding Worker requires --dsh-runner for turn run-once"
            )
        turn_argv += [
            "--dsh-runner",
            str(dsh_runner),
            "--timeout-seconds",
            str(int(timeout_seconds)),
        ]
        if dsh_home is not None:
            turn_argv += ["--dsh-home", str(dsh_home)]
    elif descriptor.turn_host == worker_catalog.TURN_HOST_CODEX_CLI:
        turn_argv += [
            "--timeout-seconds",
            str(int(timeout_seconds)),
        ]
        if codex_bin:
            turn_argv += ["--codex-bin", str(codex_bin)]
        elif descriptor.command:
            turn_argv += ["--codex-bin", str(descriptor.command[0])]
    elif descriptor.turn_host == worker_catalog.TURN_HOST_GENERIC_CLI:
        adapter = list(cursor_adapter_command or ())
        if not adapter:
            if descriptor.worker_kind == worker_catalog.WORKER_KIND_CURSOR_CLI_CODING_WORKER:
                adapter = [
                    sys.executable,
                    "-m",
                    "loopx.cursor_worker.turn_host_adapter",
                ]
            else:
                raise LoopError(
                    "generic-cli Coding Worker requires an adapter command "
                    "(--cursor-adapter-command-json)"
                )
        turn_argv += [
            "--host-adapter-command-json",
            json.dumps(adapter),
            "--timeout-seconds",
            str(int(timeout_seconds)),
        ]
    else:
        raise LoopError(f"unsupported turn_host: {descriptor.turn_host!r}")

    if validation_argv:
        turn_argv += [
            "--validation-command-json",
            json.dumps(validation_argv),
            "--validation-timeout-seconds",
            "300",
        ]
    turn_argv.append("--execute")
    return turn_argv


def _run_cli(
    argv: list[str], *, cwd: Path, timeout: float, cancel: Any = None
) -> tuple[int, str, str]:
    """Run one ``loopx`` command, terminating its process group on cancel."""

    try:
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            argv,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise LoopError("the loopx CLI is not available on PATH") from exc

    deadline = time.monotonic() + timeout
    while True:
        if _is_cancelled(cancel):
            _terminate_group(process)
            raise LoopError("the loop was cancelled while a command was running")
        try:
            out, err = process.communicate(timeout=0.25)
        except subprocess.TimeoutExpired:
            if time.monotonic() > deadline:
                _terminate_group(process)
                raise LoopError(f"loopx command timed out: {argv[1:3]}")
            continue
        return process.returncode, out or "", err or ""


def _is_cancelled(cancel: Any) -> bool:
    return cancel is not None and bool(getattr(cancel, "is_set", lambda: False)())


def _terminate_group(process: "subprocess.Popen[str]") -> None:
    """Terminate the whole process group so no host is left running."""

    try:
        group = os.getpgid(process.pid)
    except (ProcessLookupError, OSError):
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(group, sig)
        except (ProcessLookupError, OSError):
            return
        try:
            process.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


def _run_json(
    argv: list[str], *, cwd: Path, timeout: float, cancel: Any = None
) -> dict[str, Any]:
    if _is_cancelled(cancel):
        raise LoopError("the loop was cancelled before the command started")
    code, out, err = _run_cli(argv, cwd=cwd, timeout=timeout, cancel=cancel)
    text = (out or "").strip()
    if not text:
        raise LoopError(f"loopx returned no output (exit {code}): {err.strip()[:200]}")
    start = text.find("{")
    if start == -1:
        raise LoopError(f"loopx returned non-JSON output: {text[:200]}")
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError as exc:
        raise LoopError(f"loopx returned malformed JSON: {exc.msg}") from exc


def _deep_get(payload: Any, key: str, _depth: int = 0) -> Any:
    """Find a field without assuming the report's nesting depth.

    ``turn run-once`` renders a flat summary in markdown but nests the same
    facts differently under ``--format json``. Reading by name keeps the loop
    working across both shapes instead of silently treating a fence as an
    unknown failure.
    """

    if _depth > 6:
        return None
    if isinstance(payload, dict):
        if key in payload and payload[key] is not None:
            return payload[key]
        for value in payload.values():
            found = _deep_get(value, key, _depth + 1)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _deep_get(value, key, _depth + 1)
            if found is not None:
                return found
    return None


def _deep_contains(payload: Any, needle: str, _depth: int = 0) -> bool:
    if _depth > 6:
        return False
    if isinstance(payload, str):
        return payload == needle
    if isinstance(payload, dict):
        return any(_deep_contains(v, needle, _depth + 1) for v in payload.values())
    if isinstance(payload, list):
        return any(_deep_contains(v, needle, _depth + 1) for v in payload)
    return False


def _parse_turn_report(payload: dict[str, Any]) -> TurnRecord:
    """Read one ``turn run-once --format json`` report at any nesting depth."""

    phases = _deep_get(payload, "completed_phases")
    return TurnRecord(
        turn_instance_id=str(_deep_get(payload, "turn_instance_id") or ""),
        todo_id=str(_deep_get(payload, "todo_id") or ""),
        action=str(_deep_get(payload, "command") or _deep_get(payload, "action") or ""),
        status=str(_deep_get(payload, "status") or "unknown"),
        result_kind=str(_deep_get(payload, "result_kind") or "unknown"),
        validation=str(_deep_get(payload, "validation") or "unknown"),
        host_invoked=bool(_deep_get(payload, "host_invoked")),
        phases=list(phases) if isinstance(phases, list) else [],
        recovery_from=_deep_get(payload, "recovery_from"),
        stop_reason=_deep_get(payload, "error"),
    )


@dataclass
class LoopOutcome:
    turns: list[TurnRecord]
    stop_reason: str
    stop_detail: str
    #: Types the stop so AC-10 is assertable directly instead of inferred:
    #: paused | gate | quota | no_work | turn_failed | cancelled | dry_run |
    #: max_turns | not_admitted.
    stop_kind: str = "unknown"
    #: How many Turns actually reached the host. A stop that never launches the
    #: host must report zero here -- that is exactly the AC-10 claim.
    host_invocations: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LOOP_SCHEMA_VERSION,
            "turn_count": len(self.turns),
            "host_invocations": self.host_invocations,
            "turns": [turn.as_dict() for turn in self.turns],
            "stop_kind": self.stop_kind,
            "stop_reason": self.stop_reason,
            "stop_detail": self.stop_detail,
        }


#: LoopX refusal state -> loop stop kind, as recorded by ``quota should-run``.
_STOP_KIND_BY_STATE = {
    "paused": "paused",
    "user_gate": "gate",
    "blocked": "gate",
    "quota_exhausted": "quota",
    "exhausted": "quota",
    "budget_exhausted": "quota",
}


def classify_stop_kind(state: str, detail: str) -> str:
    """Classify a refusal into the typed stop kind the loop reports.

    LoopX owns the vocabulary; this only groups it so a caller (and the AC-10
    test) can assert "paused", "gate" or "quota" without string matching.
    """

    token = str(state or "").strip().lower()
    if token in _STOP_KIND_BY_STATE:
        return _STOP_KIND_BY_STATE[token]
    text = str(detail or "").lower()
    if "paused" in text or "stopped by owner" in text:
        return "paused"
    if "gate" in text:
        return "gate"
    if "quota" in text or "budget" in text:
        return "quota"
    return "not_admitted"


def _eligible_todo_ids(should_run: dict[str, Any]) -> list[str]:
    """Read LoopX's own candidate ordering; never invent one.

    LoopX publishes a *recommended* Todo plus alternatives and requires an
    explicit turn binding before any spend (``selection_policy``). The loop
    therefore follows the recommendation order rather than treating the
    publication as a hard dependency fence.
    """

    portfolio = should_run.get("action_portfolio") or {}
    ids: list[str] = []
    # LoopX reports a single eligible Todo as "<todo_id>: <text>" on the agent
    # channel and only fills the multi-action portfolio when it has alternatives.
    channel = (should_run.get("interaction_contract") or {}).get("agent_channel") or {}
    channel_action = str(channel.get("primary_action") or "").strip()
    if channel_action:
        candidate = channel_action.split(":", 1)[0].strip()
        if candidate.startswith("todo_"):
            ids.append(candidate)
    primary = str((portfolio.get("primary") or {}).get("todo_id") or "").strip()
    if primary and primary not in ids:
        ids.append(primary)
    for action in portfolio.get("suggested_actions") or []:
        todo_id = str(action.get("todo_id") or "").strip()
        if todo_id and todo_id not in ids:
            ids.append(todo_id)
    for action in portfolio.get("actions") or []:
        todo_id = str(action.get("todo_id") or "").strip()
        if todo_id and todo_id not in ids:
            ids.append(todo_id)
    return ids


#: A Turn whose host already ran but whose settlement is not committed. LoopX
#: reports this in more than one shape, and matching only one of them silently
#: turns a settleable Turn into a failure.
_SETTLEMENT_FENCE_MARKERS = (
    "durable_writeback",
    "controller-declared completion validation",
    "accountable refresh is blocked",
)


def _deep_mentions(payload: Any, needle: str, _depth: int = 0) -> bool:
    """Substring match over every string in a payload.

    ``_deep_contains`` compares whole values, which suits enum-like tokens such
    as ``durable_writeback``. The settlement fence is also reported as prose, so
    this companion matches inside sentences without loosening the exact-value
    helper other callers rely on.
    """

    if _depth > 6:
        return False
    if isinstance(payload, str):
        return needle in payload
    if isinstance(payload, Mapping):
        return any(_deep_mentions(v, needle, _depth + 1) for v in payload.values())
    if isinstance(payload, list):
        return any(_deep_mentions(v, needle, _depth + 1) for v in payload)
    return False


def _is_settlement_fence(record: "TurnRecord", report: Mapping[str, Any]) -> bool:
    """Return whether only the settlement is missing for an executed Turn.

    A Turn whose host already ran must never be re-invoked. LoopX reports that
    state in more than one shape -- an exact ``durable_writeback`` marker, or the
    prose "accountable refresh is blocked until controller-declared completion
    validation durably completes todo ..." -- and matching only one of them
    silently turned a settleable Turn into a reported failure.
    """

    if record.status == "committed":
        return False
    if record.recovery_from == "durable_writeback":
        return True
    return any(_deep_mentions(report, marker) for marker in _SETTLEMENT_FENCE_MARKERS)


def run_host_loop(
    *,
    goal_id: str,
    agent_id: str,
    project: Path,
    dsh_runner: Path | None = None,
    validation_argv: list[str] | None = None,
    validation_by_todo: dict[str, list[str]] | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    registry_path: Path | None = None,
    dsh_home: Path | None = None,
    worker_endpoint_id: str | None = None,
    codex_bin: str | None = None,
    cursor_adapter_command: Sequence[str] | None = None,
    lease_key_prefix: str = "host-loop-lease",
    lease_version: int = 1,
    dry_run: bool = False,
    cancel: Any = None,
) -> LoopOutcome:
    """Drive governed Turns until LoopX refuses to admit another one.

    ``cancel`` is any object with ``is_set()`` (a ``threading.Event``). A set
    flag stops the loop before the next Turn is asked for and terminates an
    in-flight ``loopx`` call, so an owner stop never leaves a host running.

    ``worker_endpoint_id`` selects the Coding Worker. When omitted, the Goal
    registry value is used, falling back to the historical DSH endpoint.
    """

    project = project.expanduser().resolve()
    if dsh_runner is not None:
        dsh_runner = dsh_runner.expanduser().resolve()
    base = [_loopx_bin(), "--format", "json"]
    if registry_path is not None:
        base += ["--registry", str(registry_path)]

    endpoint_id = _goal_worker_endpoint_id(
        goal_id=goal_id,
        registry_path=registry_path,
        explicit=worker_endpoint_id,
    )
    descriptor = worker_catalog.resolve_worker(
        endpoint_id,
        authorized_workspaces=[project],
        require_enabled=True,
    )
    if descriptor is None:
        unavailable = worker_catalog.worker_unavailable_reason(endpoint_id)
        if unavailable == "feature_flag_disabled":
            flag = worker_catalog.endpoint_feature_flag(endpoint_id) or (
                worker_catalog.WORKER_FEATURE_FLAG_ENV
            )
            raise LoopError(
                f"Coding Worker endpoint is disabled on this machine "
                f"(opt in with {flag}=1): {endpoint_id}"
            )
        raise LoopError(
            f"Coding Worker endpoint is not registered: {endpoint_id}"
        )
    agent_id = _resolve_host_loop_actor_id(
        goal_id=goal_id,
        registry_path=registry_path,
        explicit_agent_id=agent_id,
        endpoint_id=endpoint_id,
    )

    turns: list[TurnRecord] = []
    host_invocations = 0
    stop_reason = "max_turns_reached"
    stop_detail = f"loop stopped after {max_turns} turns"
    stop_kind = "max_turns"

    def _cancelled() -> bool:
        return _is_cancelled(cancel)

    def _validation_for(todo: str) -> list[str] | None:
        """Each Todo owns its own completion validation, never a shared guess."""

        if validation_by_todo:
            selected = validation_by_todo.get(todo)
            if selected:
                return list(selected)
        return list(validation_argv) if validation_argv else None

    for _ in range(max(1, max_turns)):
        if _cancelled():
            stop_kind = "cancelled"
            stop_reason = "cancelled"
            stop_detail = "the loop was cancelled before asking LoopX for more work"
            break
        # 1. Ask LoopX whether any work is admitted at all.
        should_run = _run_json(
            base
            + [
                "quota",
                "should-run",
                "--goal-id",
                goal_id,
                "--agent-id",
                agent_id,
                "--runtime-profile",
                "generic_cli",
            ],
            cwd=project,
            timeout=timeout_seconds,
        )
        if should_run.get("should_run") is not True:
            stop_reason = str(
                should_run.get("state") or should_run.get("decision") or "not_admitted"
            )
            stop_detail = str(should_run.get("reason") or "LoopX did not admit a Turn")
            stop_kind = classify_stop_kind(stop_reason, stop_detail)
            break

        candidates = _eligible_todo_ids(should_run)
        if not candidates:
            stop_reason = "no_eligible_todo"
            stop_detail = "LoopX admitted a Turn but published no eligible Todo"
            stop_kind = "no_work"
            break
        todo_id = candidates[0]

        # 2. Bind one Todo and one Turn identity through LoopX's own guard.
        turn_instance_id = f"turn-{goal_id}-{uuid.uuid4()}"
        bound = _run_json(
            base
            + [
                "quota",
                "should-run",
                "--goal-id",
                goal_id,
                "--agent-id",
                agent_id,
                "--runtime-profile",
                "generic_cli",
                "--todo-id",
                todo_id,
                "--turn-instance-id",
                turn_instance_id,
            ],
            cwd=project,
            timeout=timeout_seconds,
        )
        if bound.get("should_run") is not True:
            stop_reason = str(
                bound.get("state") or bound.get("decision") or "not_admitted"
            )
            stop_detail = str(
                bound.get("reason") or "LoopX refused the selected Todo"
            )
            stop_kind = classify_stop_kind(stop_reason, stop_detail)
            break

        if dry_run:
            turns.append(
                TurnRecord(
                    turn_instance_id=turn_instance_id,
                    todo_id=todo_id,
                    action="quota-bound",
                    status="dry_run",
                    result_kind="dry_run",
                    validation="not_run",
                    host_invoked=False,
                    stop_reason="dry_run",
                )
            )
            stop_reason = "dry_run"
            stop_detail = "dry run stopped before invoking the host"
            stop_kind = "dry_run"
            break

        # 3. Execute exactly one governed Turn. LoopX invokes the host, runs the
        #    independent validator, and commits only on a passing result.
        turn_validation = _validation_for(todo_id)
        turn_argv = build_turn_run_once_argv(
            base=base,
            goal_id=goal_id,
            agent_id=agent_id,
            project=project,
            todo_id=todo_id,
            turn_instance_id=turn_instance_id,
            timeout_seconds=timeout_seconds,
            descriptor=descriptor,
            dsh_runner=dsh_runner,
            dsh_home=dsh_home,
            codex_bin=codex_bin,
            cursor_adapter_command=cursor_adapter_command,
            validation_argv=turn_validation,
        )

        host_invocations += 1
        report = _run_json(
            turn_argv, cwd=project, timeout=timeout_seconds + 300, cancel=cancel
        )
        record = _parse_turn_report(report)

        # 4. Settlement fence. A Turn whose host already ran must never be
        #    re-invoked; when only the durable writeback is missing, complete the
        #    controller-declared Todo validation and resume the SAME Turn.
        if _is_settlement_fence(record, report):
            settled = _settle_prepared_turn(
                base=base,
                goal_id=goal_id,
                agent_id=agent_id,
                project=project,
                todo_id=todo_id,
                turn_instance_id=turn_instance_id,
                validation_argv=_validation_for(todo_id),
                lease_key_prefix=lease_key_prefix,
                lease_version=lease_version,
                timeout_seconds=timeout_seconds,
                registry_path=registry_path,
                turn_host=descriptor.turn_host,
                dsh_runner=dsh_runner,
                dsh_home=dsh_home,
                codex_bin=codex_bin,
                cursor_adapter_command=cursor_adapter_command,
            )
            if settled is not None:
                record = settled

        turns.append(record)

        if record.status != "committed":
            stop_reason = f"turn_{record.status}"
            stop_detail = record.stop_reason or (
                f"Turn ended as {record.result_kind} without a committed settlement"
            )
            stop_kind = "turn_failed"
            break

    return LoopOutcome(
        turns=turns,
        stop_reason=stop_reason,
        stop_detail=stop_detail,
        stop_kind=stop_kind,
        host_invocations=host_invocations,
    )


def _settle_prepared_turn(
    *,
    base: list[str],
    goal_id: str,
    agent_id: str,
    project: Path,
    todo_id: str,
    turn_instance_id: str,
    validation_argv: list[str] | None,
    lease_key_prefix: str,
    lease_version: int,
    timeout_seconds: float,
    registry_path: Path | None = None,
    turn_host: str = worker_catalog.TURN_HOST_DSH,
    dsh_runner: Path | None = None,
    dsh_home: Path | None = None,
    codex_bin: str | None = None,
    cursor_adapter_command: Sequence[str] | None = None,
) -> TurnRecord | None:
    """Resolve a prepared effect without re-invoking the host.

    Returns ``None`` when settlement could not be reached, so the caller reports
    the original Turn rather than inventing a success. ``turn_host`` must match
    the journaled plan host (Codex/Cursor resume must not hard-code ``dsh``).
    """

    if not validation_argv:
        return None

    # The controller declares the completion validation; LoopX executes it and
    # blocks the writeback until it passes.
    lease_key = f"{lease_key_prefix}-{todo_id}"
    claim = _run_json(
        base
        + [
            "todo",
            "claim",
            "--goal-id",
            goal_id,
            "--todo-id",
            todo_id,
            "--project",
            str(project),
            "--agent-id",
            agent_id,
            "--claimed-by",
            agent_id,
            "--task-lease-idempotency-key",
            lease_key,
        ],
        cwd=project,
        timeout=timeout_seconds,
    )
    lease = claim.get("lease") or {}
    expected_version = int(lease.get("version") or lease_version)

    _run_json(
        base
        + [
            "todo",
            "complete",
            "--goal-id",
            goal_id,
            "--todo-id",
            todo_id,
            "--agent-id",
            agent_id,
            "--turn-instance-id",
            turn_instance_id,
            "--status",
            "done",
            "--task-lease-idempotency-key",
            lease_key,
            "--task-lease-expected-version",
            str(expected_version),
            "--validation-command-json",
            json.dumps(validation_argv),
            "--validation-label",
            "host loop completion validation",
            *_successor_declaration(
                base=base,
                goal_id=goal_id,
                project=project,
                timeout_seconds=timeout_seconds,
            ),
        ],
        cwd=project,
        timeout=timeout_seconds,
    )

    # Re-resolve the prepared effect from the recorded journal, never by
    # re-running the host.
    journal = _in_progress_turn_key(
        goal_id=goal_id,
        registry_path=registry_path,
        project=project,
        turn_instance_id=turn_instance_id,
        todo_id=todo_id,
    )
    if journal is None:
        return None
    resume_argv = list(base) + [
        "turn",
        "run-once",
        "--goal-id",
        goal_id,
        "--agent-id",
        agent_id,
        "--host",
        turn_host,
        "--project",
        str(project),
        "--resume-turn-key",
        journal,
        "--validation-command-json",
        json.dumps(validation_argv),
        "--timeout-seconds",
        str(int(timeout_seconds)),
        "--execute",
    ]
    if turn_host == worker_catalog.TURN_HOST_DSH:
        if dsh_runner is not None:
            resume_argv += ["--dsh-runner", str(dsh_runner)]
        if dsh_home is not None:
            resume_argv += ["--dsh-home", str(dsh_home)]
    elif turn_host == worker_catalog.TURN_HOST_CODEX_CLI:
        if codex_bin:
            resume_argv += ["--codex-bin", str(codex_bin)]
    elif turn_host == worker_catalog.TURN_HOST_GENERIC_CLI:
        adapter = list(cursor_adapter_command or ())
        if not adapter:
            adapter = [
                sys.executable,
                "-m",
                "loopx.cursor_worker.turn_host_adapter",
            ]
        resume_argv += ["--host-adapter-command-json", json.dumps(adapter)]
    resumed = _run_json(
        resume_argv,
        cwd=project,
        timeout=timeout_seconds + 300,
    )
    record = _parse_turn_report(resumed)
    record.turn_instance_id = turn_instance_id
    return record


def _blocked_todo_ids(
    *, base: list[str], goal_id: str, project: Path, timeout_seconds: float
) -> list[str]:
    """List the Goal's blocked Todos using LoopX's own projection."""

    listing = _run_json(
        base
        + [
            "todo",
            "list",
            "--goal-id",
            goal_id,
            "--project",
            str(project),
            "--role",
            "agent",
            "--limit",
            "50",
        ],
        cwd=project,
        timeout=timeout_seconds,
    )
    return [
        str(todo.get("todo_id"))
        for todo in (listing.get("todos") or [])
        if str(todo.get("status") or "") == "blocked" and todo.get("todo_id")
    ]


def _successor_declaration(
    *,
    base: list[str],
    goal_id: str,
    project: Path,
    timeout_seconds: float,
) -> list[str]:
    """Declare the completed Todo's continuation to LoopX.

    LoopX raises an ``autonomous_replan_required`` obligation after a completion
    that leaves no runnable successor, and it explicitly forbids inventing a
    user gate or a filler Todo. The honest answer is therefore one of exactly
    two declarations:

    * a dependent Todo is waiting, so link it (``--successor-todo-id``), which is
      what releases a ``blocked`` dependent into eligibility;
    * nothing real remains, so say so (``--no-follow-up``).

    With more than one blocked candidate the successor is ambiguous, so the loop
    declares nothing and lets LoopX keep the obligation rather than guessing.
    """

    blocked = _blocked_todo_ids(
        base=base, goal_id=goal_id, project=project, timeout_seconds=timeout_seconds
    )
    if len(blocked) == 1:
        return ["--successor-todo-id", blocked[0]]
    if not blocked:
        return ["--no-follow-up"]
    return []


def _runtime_root_for(registry_path: Path | None, project: Path) -> Path:
    """Resolve the LoopX runtime root that holds the Turn journals."""

    candidate = registry_path or (project / ".loopx" / "registry.json")
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Path.home() / ".loopx"
    declared = str(payload.get("common_runtime_root") or "").strip()
    return Path(declared).expanduser() if declared else Path.home() / ".loopx"


def _in_progress_turn_key(
    *,
    goal_id: str,
    registry_path: Path | None,
    project: Path,
    turn_instance_id: str | None = None,
    todo_id: str | None = None,
) -> str | None:
    """Find the not-yet-terminal Turn that belongs to this exact Turn.

    Reading the journal is the documented way to settle a prepared effect: the
    PRD requires resolving an interrupted settlement through the *existing*
    Turn identity and log rather than by re-running a host that already had side
    effects. The journal is read-only here; nothing is edited.

    Identity is matched on the recorded ``plan.transaction.turn_instance_id``
    (cross-checked against the Todo) so a stale non-terminal journal left by an
    earlier attempt can never be mistaken for this Turn. An ambiguous match
    returns ``None``: refusing to settle is safer than settling the wrong Turn.
    """

    turns_dir = _runtime_root_for(registry_path, project) / "goals" / goal_id / "turns"
    if not turns_dir.is_dir():
        return None

    candidates: list[tuple[str, str, str | None]] = []
    for path in sorted(turns_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("status") != "in_progress":
            continue
        turn_key = str(payload.get("turn_key") or "")
        if not turn_key:
            continue
        recorded_turn = str(_deep_get(payload, "turn_instance_id") or "")
        recorded_todo = str(_deep_get(payload, "todo_id") or "")
        candidates.append((turn_key, recorded_turn, recorded_todo or None))

    if not candidates:
        return None

    if turn_instance_id is not None:
        exact = [
            key
            for key, recorded_turn, recorded_todo in candidates
            if recorded_turn == turn_instance_id
            and (todo_id is None or recorded_todo is None or recorded_todo == todo_id)
        ]
        return exact[0] if len(exact) == 1 else None

    # No identity supplied: only a single unambiguous candidate may be settled.
    return candidates[0][0] if len(candidates) == 1 else None


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--goal-id", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument(
        "--dsh-runner",
        default=None,
        help="Required when the selected endpoint uses --host dsh.",
    )
    parser.add_argument("--worker-endpoint-id", default=None)
    parser.add_argument("--codex-bin", default=None)
    parser.add_argument(
        "--cursor-adapter-command-json",
        default=None,
        help="JSON argv for the Cursor generic-cli adapter.",
    )
    parser.add_argument("--validation-command-json", default=None)
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--registry", default=None)
    parser.add_argument("--dsh-home", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    outcome = run_host_loop(
        goal_id=args.goal_id,
        agent_id=args.agent_id,
        project=Path(args.project),
        dsh_runner=Path(args.dsh_runner) if args.dsh_runner else None,
        validation_argv=(
            json.loads(args.validation_command_json)
            if args.validation_command_json
            else None
        ),
        max_turns=args.max_turns,
        timeout_seconds=args.timeout_seconds,
        registry_path=Path(args.registry) if args.registry else None,
        dsh_home=Path(args.dsh_home) if args.dsh_home else None,
        worker_endpoint_id=args.worker_endpoint_id,
        codex_bin=args.codex_bin,
        cursor_adapter_command=(
            json.loads(args.cursor_adapter_command_json)
            if args.cursor_adapter_command_json
            else None
        ),
        dry_run=args.dry_run,
    )
    json.dump(outcome.as_dict(), __import__("sys").stdout, ensure_ascii=False, indent=1)
    __import__("sys").stdout.write("\n")
    return 0 if outcome.stop_reason in {"no_eligible_todo", "dry_run"} else 1


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())

"""Governed Turn host loop for the DSH Coding Worker.

What this is
------------
LoopX owns Goal/Todo identity, eligibility, quota, validation and writeback. It
deliberately does *not* own a timer: for a generic CLI host it expects the host
to provide the loop that asks "should I run?" and then obeys the answer. This
module is that loop for the DSH Coding Worker.

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
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOOP_SCHEMA_VERSION = "loopx_dsh_coding_worker_loop_v1"
DEFAULT_MAX_TURNS = 8
DEFAULT_TIMEOUT_SECONDS = 1_800


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


def _run_cli(
    argv: list[str], *, cwd: Path, timeout: float
) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise LoopError(f"loopx command timed out: {argv[1:3]}") from exc
    except FileNotFoundError as exc:
        raise LoopError("the loopx CLI is not available on PATH") from exc
    return completed.returncode, completed.stdout, completed.stderr


def _run_json(
    argv: list[str], *, cwd: Path, timeout: float
) -> dict[str, Any]:
    code, out, err = _run_cli(argv, cwd=cwd, timeout=timeout)
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LOOP_SCHEMA_VERSION,
            "turn_count": len(self.turns),
            "turns": [turn.as_dict() for turn in self.turns],
            "stop_reason": self.stop_reason,
            "stop_detail": self.stop_detail,
        }


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


def run_host_loop(
    *,
    goal_id: str,
    agent_id: str,
    project: Path,
    dsh_runner: Path,
    validation_argv: list[str] | None = None,
    validation_by_todo: dict[str, list[str]] | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    registry_path: Path | None = None,
    dsh_home: Path | None = None,
    lease_key_prefix: str = "host-loop-lease",
    lease_version: int = 1,
    dry_run: bool = False,
) -> LoopOutcome:
    """Drive governed Turns until LoopX refuses to admit another one."""

    project = project.expanduser().resolve()
    dsh_runner = dsh_runner.expanduser().resolve()
    base = [_loopx_bin(), "--format", "json"]
    if registry_path is not None:
        base += ["--registry", str(registry_path)]

    turns: list[TurnRecord] = []
    stop_reason = "max_turns_reached"
    stop_detail = f"loop stopped after {max_turns} turns"

    def _validation_for(todo: str) -> list[str] | None:
        """Each Todo owns its own completion validation, never a shared guess."""

        if validation_by_todo:
            selected = validation_by_todo.get(todo)
            if selected:
                return list(selected)
        return list(validation_argv) if validation_argv else None

    for _ in range(max(1, max_turns)):
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
            break

        candidates = _eligible_todo_ids(should_run)
        if not candidates:
            stop_reason = "no_eligible_todo"
            stop_detail = "LoopX admitted a Turn but published no eligible Todo"
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
            break

        # 3. Execute exactly one governed Turn. LoopX invokes the host, runs the
        #    independent validator, and commits only on a passing result.
        turn_argv = base + [
            "turn",
            "run-once",
            "--goal-id",
            goal_id,
            "--agent-id",
            agent_id,
            "--host",
            "dsh",
            "--project",
            str(project),
            "--todo-id",
            todo_id,
            "--turn-instance-id",
            turn_instance_id,
            "--dsh-runner",
            str(dsh_runner),
            "--timeout-seconds",
            str(int(timeout_seconds)),
        ]
        if dsh_home is not None:
            turn_argv += ["--dsh-home", str(dsh_home)]
        turn_validation = _validation_for(todo_id)
        if turn_validation:
            turn_argv += [
                "--validation-command-json",
                json.dumps(turn_validation),
                "--validation-timeout-seconds",
                "300",
            ]
        turn_argv.append("--execute")

        report = _run_json(turn_argv, cwd=project, timeout=timeout_seconds + 300)
        record = _parse_turn_report(report)

        # 4. Settlement fence. A Turn whose host already ran must never be
        #    re-invoked; when only the durable writeback is missing, complete the
        #    controller-declared Todo validation and resume the SAME Turn.
        if record.status != "committed" and (
            record.recovery_from == "durable_writeback"
            or _deep_contains(report, "durable_writeback")
        ):
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
            )
            if settled is not None:
                record = settled

        turns.append(record)

        if record.status != "committed":
            stop_reason = f"turn_{record.status}"
            stop_detail = record.stop_reason or (
                f"Turn ended as {record.result_kind} without a committed settlement"
            )
            break
    else:
        return LoopOutcome(turns=turns, stop_reason=stop_reason, stop_detail=stop_detail)

    return LoopOutcome(turns=turns, stop_reason=stop_reason, stop_detail=stop_detail)


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
) -> TurnRecord | None:
    """Resolve a prepared effect without re-invoking the host.

    Returns ``None`` when settlement could not be reached, so the caller reports
    the original Turn rather than inventing a success.
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
        goal_id=goal_id, registry_path=registry_path, project=project
    )
    if journal is None:
        return None
    resumed = _run_json(
        base
        + [
            "turn",
            "run-once",
            "--goal-id",
            goal_id,
            "--agent-id",
            agent_id,
            "--host",
            "dsh",
            "--project",
            str(project),
            "--resume-turn-key",
            journal,
            "--validation-command-json",
            json.dumps(validation_argv),
            "--timeout-seconds",
            str(int(timeout_seconds)),
            "--execute",
        ],
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
) -> str | None:
    """Find the not-yet-terminal Turn by reading the recorded Turn journal.

    Reading the journal is the documented way to settle a prepared effect: the
    PRD requires resolving an interrupted settlement through the *existing*
    Turn identity and log rather than by re-running a host that already had side
    effects. The journal is a read-only artifact here; nothing is edited.
    """

    turns_dir = _runtime_root_for(registry_path, project) / "goals" / goal_id / "turns"
    if not turns_dir.is_dir():
        return None
    for path in sorted(turns_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("status") == "in_progress" and payload.get("turn_key"):
            return str(payload["turn_key"])
    return None


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--goal-id", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--dsh-runner", required=True)
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
        dsh_runner=Path(args.dsh_runner),
        validation_argv=(
            json.loads(args.validation_command_json)
            if args.validation_command_json
            else None
        ),
        max_turns=args.max_turns,
        timeout_seconds=args.timeout_seconds,
        registry_path=Path(args.registry) if args.registry else None,
        dsh_home=Path(args.dsh_home) if args.dsh_home else None,
        dry_run=args.dry_run,
    )
    json.dump(outcome.as_dict(), __import__("sys").stdout, ensure_ascii=False, indent=1)
    __import__("sys").stdout.write("\n")
    return 0 if outcome.stop_reason in {"no_eligible_todo", "dry_run"} else 1


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())

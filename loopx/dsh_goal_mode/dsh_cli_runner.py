"""DeepSeek Harness 0.2 CLI runner for one LoopX-governed Turn.

This module is the CLI counterpart of the in-process SDK host
(``loopx.dsh_goal_mode.turn_host_adapter``). It implements the same seam: the
``run_dsh_turn`` callable that ``load_dsh_runner`` looks for, returning a
``final_response`` / ``finish_reason`` / ``events`` outcome. Registering it as
the DSH runner is what makes ``loopx turn run-once --host dsh`` drive the npm
CLI ``0.2.0-rc.2`` instead of the pinned Python ``deepseek-harness-sdk``.

Two measured properties of the installed launcher shape this module:

**The argv surface is tiny.** ``dsh --profile headless`` accepts only
``--json``, ``--session-id``, ``--patch`` and the task (stdin or positional).
Model, provider and reasoning effort are applied through an isolated per-Turn
cordis ``--patch`` file when the caller supplies them. ``max_tokens`` remains
unsupported on the measured 0.2 CLI argv and is still recorded rather than
pretended. Shared ``$DSH_HOME/profiles/headless`` is never mutated.

**Session adoption is conditional.** ``--session-id`` fails closed when no
persisted Session with that id exists, and it refuses a Session recorded in a
different working directory. The adapter therefore defaults to a fresh Session
per Turn (the PRD's R-03 posture) and only adopts when the caller opts in *and*
the persisted Session verifies.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def _load_sibling(stem: str) -> Any:
    """Load one sibling module from this file's own directory.

    The governed-turn adapter loads this runner by *file path*
    (``turn_host_adapter.load_dsh_runner``), and LoopX itself may be executing
    from an installed release snapshot rather than from this checkout. An
    absolute ``loopx.*`` import would then resolve against that other
    installation -- or fail outright with ``ModuleNotFoundError``, which LoopX
    reports as an ``unknown`` host failure. Loading the codec and supervisor
    from this directory keeps the CLI host self-consistent wherever it lives and
    guarantees it never borrows a differently-versioned codec.
    """

    sibling = Path(__file__).resolve().with_name(f"{stem}.py")
    module_name = f"_loopx_dsh_cli_{stem}"
    spec = importlib.util.spec_from_file_location(module_name, sibling)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load dsh CLI host sibling module: {sibling}")
    module = importlib.util.module_from_spec(spec)
    # `@dataclass` resolves `cls.__module__` through `sys.modules`, so the
    # module must be registered before it executes.
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_ndjson = _load_sibling("dsh_cli_ndjson")
_process = _load_sibling("dsh_cli_process")

COMPLETED_TURN_KIND = _ndjson.COMPLETED_TURN_KIND
DshCliProtocolError = _ndjson.DshCliProtocolError
describe_stream = _ndjson.describe_stream
final_text = _ndjson.final_text
format_diagnostics = _ndjson.format_diagnostics
parse_event_stream = _ndjson.parse_event_stream
retained_events = _ndjson.retained_events
session_identity = _ndjson.session_identity
terminal_turn_kind = _ndjson.terminal_turn_kind
terminal_turn_reason = _ndjson.terminal_turn_reason
to_sdk_events = _ndjson.to_sdk_events

DEFAULT_IDLE_TIMEOUT_SECONDS = _process.DEFAULT_IDLE_TIMEOUT_SECONDS
DEFAULT_WALL_TIMEOUT_SECONDS = _process.DEFAULT_WALL_TIMEOUT_SECONDS
DshCliInvocation = _process.DshCliInvocation
DshCliProcessError = _process.DshCliProcessError
run_invocation = _process.run_invocation

LOOPX_TURN_HOST_REQUEST_SCHEMA = "loopx_turn_host_request_v0"
LOOPX_TURN_RESULT_SCHEMA = "loopx_turn_result_v0"
DSH_CLI_HOST_NAME = "DSH CLI 0.2"

#: Controls the measured 0.2 CLI still cannot express even with ``--patch``.
UNSUPPORTED_ARGV_CONTROLS = ("max_tokens",)

#: Environment switch that opts into Session adoption instead of a fresh run.
SESSION_MODE_ENV = "LOOPX_DSH_CLI_SESSION_MODE"
SESSION_MODE_FRESH = "fresh"
SESSION_MODE_RESUME = "resume"


class DshCliTurnError(RuntimeError):
    """A CLI Turn failed before a trustworthy typed result existed.

    ``code`` uses LoopX's stable host-failure vocabulary so the existing
    classifier records retryability instead of ``unknown``.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def resolve_dsh_binary(runtime_bin: str | None) -> str:
    return str(runtime_bin).strip() if runtime_bin else "dsh"


def persisted_session_dirs(dsh_home: Path | None, session_id: str) -> list[Path]:
    """Return persisted Session directories matching ``session_id``.

    The launcher stores a Session under ``<dsh_home>/sessions/<cwd-slug>/<id>/``.
    An empty result means adoption must not be attempted: ``--session-id`` would
    fail the run outright rather than start clean.
    """

    if not session_id or dsh_home is None:
        return []
    root = dsh_home.expanduser() / "sessions"
    if not root.is_dir():
        return []
    return sorted(path for path in root.glob(f"*/{session_id}") if path.is_dir())


def build_argv(
    *,
    dsh_bin: str,
    session_id: str | None,
    cordis: Path | None,
    resume: bool,
) -> tuple[str, ...]:
    """Build the argv array. No shell string is ever composed."""

    argv: list[str] = [dsh_bin, "--profile", "headless", "--json"]
    if cordis is not None:
        argv.extend(["--patch", str(cordis.expanduser().resolve())])
    if resume and session_id:
        argv.extend(["--session-id", session_id])
    # A lone `-` selects the stdin task marker.
    argv.append("-")
    return tuple(argv)


def write_turn_model_cordis_patch(
    directory: Path,
    *,
    provider: str | None,
    model: str | None,
    reasoning_effort: str | None,
) -> Path | None:
    """Write one Turn-private cordis patch and return its path.

    Returns ``None`` when no model controls were requested. The file lives under
    ``directory`` (a Turn-private workspace) so concurrent Turns cannot share or
    mutate ``$DSH_HOME/profiles``.
    """

    if not any((provider, model, reasoning_effort)):
        return None
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "loopx-turn-model.cordis.yml"
    lines = [
        "# Generated by LoopX for one governed Turn. Do not reuse across Turns.",
        "plugins:",
        "  '@deepseek-ai/dsh-llm':",
    ]
    if provider:
        lines.append(f"    provider: {provider}")
    if model:
        lines.append(f"    model: {model}")
    if reasoning_effort:
        lines.append(f"    reasoningEffort: {reasoning_effort}")
    lines.append("")
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def run_dsh_turn(
    *,
    prompt: str,
    session_id: str | None = None,
    workspace: Path | str | None = None,
    session_root: Path | str | None = None,
    provider: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    max_tokens: int | None = None,
    cordis: Path | str | None = None,
    runtime_bin: str | None = None,
    request_timeout_seconds: float | None = None,
    env: Mapping[str, str] | None = None,
    cancel: Any = None,
    on_started: Any = None,
) -> dict[str, Any]:
    """Execute one bounded Turn through ``dsh --profile headless --json``.

    Returns the outcome mapping the LoopX adapter expects. Raises
    :class:`DshCliTurnError` when no trustworthy candidate exists, so the Turn
    journal records a typed failure rather than a fabricated success.
    """

    if not isinstance(prompt, str) or not prompt.strip():
        raise DshCliTurnError("invalid_request", "empty Turn prompt")

    workdir = Path(workspace or os.getcwd()).expanduser().resolve()
    if not workdir.is_dir():
        raise DshCliTurnError(
            "invalid_request", f"authorized workspace does not exist: {workdir}"
        )
    dsh_home = Path(session_root).expanduser().resolve() if session_root else None

    session_mode = os.environ.get(SESSION_MODE_ENV, SESSION_MODE_FRESH).strip().lower()
    persisted = persisted_session_dirs(dsh_home, str(session_id or ""))
    resume = (
        session_mode == SESSION_MODE_RESUME
        and bool(session_id)
        and bool(persisted)
    )
    if session_mode == SESSION_MODE_RESUME and session_id and not persisted:
        # Explicitly requested adoption of an unknown Session: fail closed
        # instead of silently starting a different conversation. `invalid_request`
        # classifies as contract_rejected, i.e. non-retryable.
        raise DshCliTurnError(
            "invalid_request",
            f"session_missing: session {session_id} is not persisted under the "
            "configured DSH home",
        )

    patch_path = Path(cordis).expanduser().resolve() if cordis else None
    applied_model_controls: dict[str, str] = {}
    if patch_path is None:
        patch_dir = workdir / ".loopx" / "dsh-turn-patches" / (
            str(session_id or "fresh").replace("/", "_")[:64]
        )
        generated = write_turn_model_cordis_patch(
            patch_dir,
            provider=provider,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        if generated is not None:
            patch_path = generated
            if provider:
                applied_model_controls["provider"] = provider
            if model:
                applied_model_controls["model"] = model
            if reasoning_effort:
                applied_model_controls["reasoning_effort"] = reasoning_effort
    elif any((provider, model, reasoning_effort)):
        # Caller supplied an explicit cordis file; record the intended controls
        # as applied-by-patch without rewriting that file.
        if provider:
            applied_model_controls["provider"] = provider
        if model:
            applied_model_controls["model"] = model
        if reasoning_effort:
            applied_model_controls["reasoning_effort"] = reasoning_effort

    argv = build_argv(
        dsh_bin=resolve_dsh_binary(runtime_bin),
        session_id=str(session_id) if session_id else None,
        cordis=patch_path,
        resume=resume,
    )

    wall_timeout = request_timeout_seconds or DEFAULT_WALL_TIMEOUT_SECONDS
    idle_timeout = min(DEFAULT_IDLE_TIMEOUT_SECONDS, float(wall_timeout))

    try:
        outcome = run_invocation(
            DshCliInvocation(
                argv=argv,
                cwd=workdir,
                stdin_text=prompt,
                env={str(k): str(v) for k, v in (env or {}).items()} or None,
                wall_timeout_seconds=float(wall_timeout),
                idle_timeout_seconds=float(idle_timeout),
            ),
            cancel=cancel,
            on_started=on_started,
        )
    except DshCliProcessError as exc:
        raise DshCliTurnError(exc.code, str(exc)) from exc

    diagnostics: dict[str, Any] = {
        "execution_program": "dsh_cli_headless",
        "argv_program": argv[0],
        "argv_option_count": len(argv) - 2,
        "pid": outcome.pid,
        "duration_seconds": round(outcome.duration_seconds, 3),
        "first_event_seconds": (
            round(outcome.first_event_seconds, 3)
            if outcome.first_event_seconds is not None
            else None
        ),
        "last_event_seconds": (
            round(outcome.last_event_seconds, 3)
            if outcome.last_event_seconds is not None
            else None
        ),
        "session_mode": SESSION_MODE_RESUME if resume else SESSION_MODE_FRESH,
        "resume_requested": session_mode == SESSION_MODE_RESUME,
        "applied_model_controls": applied_model_controls,
        "model_patch_applied": bool(applied_model_controls and patch_path is not None),
        "unsupported_argv_controls": {
            name: value
            for name, value in (("max_tokens", max_tokens),)
            if value is not None
        },
        "stdout_truncated": outcome.stdout_truncated,
        "stderr_truncated": outcome.stderr_truncated,
    }

    if outcome.timeout_kind == "wall_timeout":
        raise DshCliTurnError(
            "timeout", f"dsh exceeded the wall budget ({wall_timeout}s): {diagnostics}"
        )
    if outcome.timeout_kind == "idle_timeout":
        raise DshCliTurnError(
            "llm_stream_idle_timeout",
            f"dsh produced no stdout activity for {idle_timeout}s",
        )
    if outcome.cancelled:
        raise DshCliTurnError("aborted", "the Turn was cancelled by its owner")
    if outcome.stdout_truncated:
        raise DshCliTurnError(
            "malformed_response",
            "dsh stdout exceeded the capture bound; the stream is truncated and "
            "cannot be trusted as a complete run",
        )

    try:
        events = parse_event_stream(outcome.stdout)
    except DshCliProtocolError as exc:
        raise DshCliTurnError(
            "malformed_response",
            f"dsh stdout is not a valid NDJSON event stream: {exc}",
        ) from exc

    diagnostics.update(describe_stream(events))
    diagnostics["exit_code"] = outcome.returncode

    reported = session_identity(events)
    diagnostics["reported_session_id"] = reported["session_id"]
    diagnostics["reported_cwd"] = reported["cwd"]

    # Workspace identity: a Turn must not silently run in another directory.
    if reported["cwd"] and Path(reported["cwd"]).expanduser().resolve() != workdir:
        raise DshCliTurnError(
            "contract_rejected",
            "dsh reported a working directory outside the authorized workspace",
        )
    # Session identity: adoption must land on the exact requested Session.
    if resume and reported["session_id"] and reported["session_id"] != session_id:
        raise DshCliTurnError(
            "contract_rejected",
            "dsh adopted a different Session than the one authorized for this Turn",
        )

    terminal_kind = terminal_turn_kind(events)
    failed = (
        outcome.returncode != 0
        or terminal_kind is None
        or terminal_kind != COMPLETED_TURN_KIND
    )
    if failed:
        reason = terminal_turn_reason(events) or {}
        if not reason:
            reason = {
                "kind": "error",
                "error": {
                    "code": _code_for_exit(outcome.returncode, outcome.stderr),
                    "message": format_diagnostics(outcome.stderr)
                    or "dsh ended without a terminal turn_end reason",
                },
            }
        diagnostics["failure_reason"] = reason
        # Hand LoopX its own fail-closed path: a terminal `error` reason and a
        # finish_reason that agrees with it. Real terminal events are retained;
        # a synthesized reason is appended only when the stream had none.
        projected = to_sdk_events(retained_events(events))
        if terminal_turn_reason(events) is None:
            projected.append({"type": "turn/end", "data": {"reason": reason}})
        else:
            projected[-1] = {"type": "turn/end", "data": {"reason": reason}}
        return {
            "final_response": final_text(events),
            "finish_reason": str(reason.get("kind") or "error"),
            "events": projected,
            "diagnostics": diagnostics,
        }

    return {
        "final_response": final_text(events),
        "finish_reason": COMPLETED_TURN_KIND,
        "events": to_sdk_events(retained_events(events)),
        "diagnostics": diagnostics,
    }


def _code_for_exit(returncode: int | None, stderr: str) -> str:
    """Map a non-zero exit onto LoopX's stable provider-code vocabulary."""

    text = (stderr or "").lower()
    if "does not exist" in text:
        return "aborted"
    if "auth" in text or "api key" in text or "credential" in text:
        return "auth"
    if "rate limit" in text or "too many requests" in text:
        return "rate_limit"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "capacity" in text or "overloaded" in text:
        return "overloaded"
    if returncode is None:
        return "transport"
    return "aborted"


def main(argv: Sequence[str] | None = None) -> int:
    """Standalone adapter entry point: one request in, one result out.

    Mirrors the in-process adapter's contract so the CLI host can also be run
    as an argv-invoked adapter by an operator or a hermetic test.
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.getcwd())
    parser.add_argument("--dsh-home", "--session-root", dest="dsh_home", default=None)
    parser.add_argument("--dsh-bin", "--runtime-bin", dest="dsh_bin", default=None)
    parser.add_argument("--cordis", default=None)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--reasoning-effort", default=None)
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--request-timeout-seconds", type=float, default=None)
    args = parser.parse_args(argv)

    from loopx.control_plane.turn_driver.host_candidate import (
        build_result,
        extract_turn_authority,
        parse_model_json,
        render_prompt,
    )

    try:
        request = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"adapter: invalid request JSON on stdin: {exc}", file=sys.stderr)
        return 2
    if (
        not isinstance(request, dict)
        or request.get("schema_version") != LOOPX_TURN_HOST_REQUEST_SCHEMA
    ):
        print(
            "adapter: stdin is not a loopx_turn_host_request_v0 object",
            file=sys.stderr,
        )
        return 2

    try:
        authority = extract_turn_authority(request)
    except ValueError as exc:
        print(f"adapter: invalid TurnEnvelope authority: {exc}", file=sys.stderr)
        return 2

    try:
        outcome = run_dsh_turn(
            prompt=render_prompt(authority),
            session_id=str(request.get("turn_key") or ""),
            workspace=Path(args.workspace),
            session_root=Path(args.dsh_home) if args.dsh_home else None,
            provider=args.provider,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            max_tokens=args.max_tokens,
            cordis=Path(args.cordis) if args.cordis else None,
            runtime_bin=args.dsh_bin,
            request_timeout_seconds=args.request_timeout_seconds,
        )
    except DshCliTurnError as exc:
        result = build_result(
            request,
            {
                "result_kind": "iteration_failed",
                "classification": exc.code,
                "summary": "The DSH CLI host could not produce a trustworthy typed result.",
                "next_action": (
                    "Inspect the retained local dsh session and the recorded failure "
                    "class before retrying; do not blindly rerun."
                ),
                "vision_unchanged_reason": "the CLI Turn did not complete",
            },
            host_name=DSH_CLI_HOST_NAME,
        )
        json.dump(result, sys.stdout, ensure_ascii=False, sort_keys=True)
        sys.stdout.write("\n")
        print(f"adapter: {exc.code}: {exc}", file=sys.stderr)
        return 1

    candidate = parse_model_json(str(outcome.get("final_response") or ""))
    result = build_result(
        request,
        candidate,
        fallback_reason=(
            "dsh CLI returned no typed JSON result; inspect the retained local session"
        ),
        host_name=DSH_CLI_HOST_NAME,
    )
    json.dump(result, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())

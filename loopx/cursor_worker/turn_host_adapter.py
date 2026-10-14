"""Cursor CLI generic-cli adapter for one LoopX-governed Turn.

LoopX invokes this module through ``turn run-once --host generic-cli`` with one
``loopx_turn_host_request_v0`` JSON object on stdin. The adapter:

1. resolves the Cursor binary (``agent`` preferred, then ``cursor-agent``),
2. runs a non-interactive print session with explicit ``--model`` and
   ``--output-format stream-json``,
3. converts Cursor stream events into exactly one ``loopx_turn_result_v0``,
4. fails closed on non-zero exit, stderr-only runs, missing result events, or
   malformed JSON.

It does not enable force/yolo, auto-trust, or "resume most recent session".
Cross-model resume is always a fresh session.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from loopx.control_plane.turn_driver.host_candidate import (
    ACCEPTED_RESULT_KINDS,
    LOOPX_TURN_HOST_REQUEST_SCHEMA,
    build_result as build_host_result,
    extract_turn_authority,
)
from loopx.extensions.process_runtime import run_capped_process

CURSOR_OUTPUT_LIMIT_BYTES = 2_000_000
CURSOR_CANDIDATE_BINARIES = ("agent", "cursor-agent")


def resolve_cursor_binary(
    *,
    preferred: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> str:
    """Return the first Cursor CLI binary that resolves on PATH."""

    environ = dict(environment if environment is not None else os.environ)
    override = str(environ.get("LOOPX_CURSOR_CODING_WORKER_BIN") or "").strip()
    candidates: list[str] = []
    if preferred:
        candidates.append(preferred)
    if override:
        candidates.append(override)
    candidates.extend(CURSOR_CANDIDATE_BINARIES)
    seen: set[str] = set()
    for name in candidates:
        if name in seen:
            continue
        seen.add(name)
        found = shutil.which(name, path=environ.get("PATH"))
        if found:
            return found
    raise FileNotFoundError(
        "neither agent nor cursor-agent found on PATH; install Cursor CLI first"
    )


def render_prompt(authority: Mapping[str, Any]) -> str:
    authority_json = json.dumps(
        dict(authority),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        "You are executing one bounded LoopX-governed work segment.\n"
        "The JSON below is the complete host authority for this Turn. Execute "
        "primary_action only after required_reads, write only inside write_scope, "
        "and obey workspace_guard. Do not infer authority from other prose.\n\n"
        f"Turn authority JSON:\n{authority_json}\n\n"
        "When finished, emit a final assistant message that includes a single "
        "JSON object with these public-safe fields:\n"
        "- result_kind: one of validated_progress | repair_required | "
        "replan_required | user_action_required | wait\n"
        "- classification (<=120 chars)\n"
        "- summary (<=400 chars)\n"
        "- recommended_action (<=1200 chars)\n"
        "- next_action (<=1200 chars)\n"
        "- vision_unchanged_reason (<=240 chars)\n"
        "Do not include credentials, absolute local paths, or raw tool dumps."
    )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def extract_candidate_from_stream(stdout: str) -> dict[str, Any] | None:
    """Parse Cursor stream-json events and recover a typed candidate.

    Accepts either a final ``result`` event with structured fields, or a JSON
    object embedded in the last assistant text. Anything else returns ``None``
    so the caller can fail closed as ``wait``.
    """

    last_text = ""
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, Mapping):
            continue
        event_type = str(event.get("type") or event.get("event") or "")
        if event_type in {"result", "turn_end", "final"}:
            payload = event.get("result") if isinstance(event.get("result"), Mapping) else event
            kind = str(payload.get("result_kind") or "")
            if kind in ACCEPTED_RESULT_KINDS:
                return dict(payload)
        if event_type in {"assistant", "message", "agent_message"}:
            text = event.get("text") or event.get("message") or event.get("content")
            if isinstance(text, str) and text.strip():
                last_text = text.strip()
            elif isinstance(text, list):
                parts = [
                    str(part.get("text") or "")
                    for part in text
                    if isinstance(part, Mapping)
                ]
                joined = "".join(parts).strip()
                if joined:
                    last_text = joined
    if not last_text:
        return None
    # Prefer a fenced or bare JSON object in the final assistant text.
    start = last_text.find("{")
    end = last_text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(last_text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if (
        isinstance(payload, Mapping)
        and str(payload.get("result_kind") or "") in ACCEPTED_RESULT_KINDS
    ):
        return dict(payload)
    return None


def build_cursor_argv(
    *,
    binary: str,
    workspace: Path,
    model: str,
    session_id: str | None,
    prompt: str,
) -> list[str]:
    """Build the Cursor CLI argv for one isolated headless Turn."""

    argv = [
        binary,
        "--print",
        "--output-format",
        "stream-json",
        "--workspace",
        str(workspace),
        "--model",
        model,
    ]
    if session_id:
        argv.extend(["--resume", session_id])
    argv.append(prompt)
    return argv


def run_cursor_turn(
    *,
    prompt: str,
    workspace: Path,
    model: str,
    session_id: str | None = None,
    binary: str | None = None,
    timeout_seconds: float = 600.0,
    environment: Mapping[str, str] | None = None,
) -> tuple[int, str, str, list[str]]:
    """Execute one Cursor Turn and return exit code, stdout, stderr, argv."""

    resolved = binary or resolve_cursor_binary(environment=environment)
    argv = build_cursor_argv(
        binary=resolved,
        workspace=workspace,
        model=model,
        session_id=session_id,
        prompt=prompt,
    )
    completed = run_capped_process(
        argv,
        stdin=b"",
        cwd=workspace,
        timeout_seconds=max(1.0, timeout_seconds),
        output_limit_bytes=CURSOR_OUTPUT_LIMIT_BYTES,
        env=dict(environment) if environment is not None else None,
    )
    if completed.failure_kind is not None:
        raise OSError(f"cursor process failed: {completed.failure_kind}")
    stdout = completed.stdout.decode("utf-8", errors="replace")
    # CapedProcessResult does not surface stderr bytes; empty stdout still
    # rejects stderr-only runs because Cursor stream-json must appear on stdout.
    return int(completed.returncode or 0), stdout, "", argv


def _request_model(request: Mapping[str, Any], cli_model: str | None) -> str:
    package = _mapping(request.get("execution_package"))
    model = (
        cli_model
        or package.get("model_applied")
        or package.get("model_resolved")
        or package.get("model")
    )
    text = str(model or "").strip()
    if not text:
        raise ValueError("Cursor Coding Worker requires an explicit model")
    return text


def _request_session_id(request: Mapping[str, Any]) -> str | None:
    session = _mapping(request.get("session"))
    context = _mapping(session.get("context_policy"))
    if str(context.get("mode") or "") == "fresh":
        return None
    session_id = str(session.get("session_id") or "").strip()
    return session_id or None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cursor-bin", default=None)
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    args = parser.parse_args(argv)

    try:
        request = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"adapter: invalid request JSON on stdin: {exc}", file=sys.stderr)
        return 2
    if (
        not isinstance(request, dict)
        or request.get("schema_version") != LOOPX_TURN_HOST_REQUEST_SCHEMA
    ):
        print("adapter: stdin is not a loopx_turn_host_request_v0 object", file=sys.stderr)
        return 2

    try:
        authority = extract_turn_authority(request)
        model = _request_model(request, args.model)
    except ValueError as exc:
        print(f"adapter: {exc}", file=sys.stderr)
        return 2

    workspace = Path(args.workspace or os.getcwd()).expanduser().resolve()
    session_id = _request_session_id(request)
    try:
        code, stdout, stderr, _argv = run_cursor_turn(
            prompt=render_prompt(authority),
            workspace=workspace,
            model=model,
            session_id=session_id,
            binary=args.cursor_bin,
            timeout_seconds=args.timeout_seconds,
        )
    except FileNotFoundError as exc:
        print(f"adapter: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"adapter: cursor exec failed: {type(exc).__name__}", file=sys.stderr)
        return 1

    if code != 0:
        print(f"adapter: cursor exited {code}", file=sys.stderr)
        if stderr.strip():
            print(stderr.strip()[:1000], file=sys.stderr)
        return code if code > 0 else 1
    if not stdout.strip():
        # stderr-only output cannot be treated as success.
        print("adapter: cursor produced no stdout stream-json", file=sys.stderr)
        if stderr.strip():
            print(stderr.strip()[:1000], file=sys.stderr)
        return 1

    candidate = extract_candidate_from_stream(stdout)
    result = build_host_result(
        request,
        candidate,
        host_name="Cursor CLI",
        fallback_reason=(
            "Cursor returned no typed result event; inspect the host session."
            if candidate is None
            else ""
        ),
    )
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

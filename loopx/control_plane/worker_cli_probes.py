"""Lightweight readiness probes for non-DSH Coding Worker CLIs.

DSH keeps its measured headless composition probe in ``dsh_cli_probe``. Codex
and Cursor only need a fail-closed binary/version check at admission time;
model catalogues and authentication remain capability declarations that the
caller validates separately.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

PROBE_SCHEMA_VERSION = "loopx_worker_cli_probe_v0"


def _run_version(
    command: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path,
    timeout_seconds: float = 8.0,
) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            list(command),
            cwd=str(cwd),
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError:
        return False, "binary_not_found"
    except subprocess.TimeoutExpired:
        return False, "version_probe_timeout"
    except OSError as exc:
        return False, f"version_probe_os_error:{type(exc).__name__}"
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[:200]
        return False, detail or f"version_exit_{completed.returncode}"
    text = (completed.stdout or completed.stderr or "").strip()
    return True, text.splitlines()[0][:200] if text else "version_ok"


def _base_result(
    *,
    ready: bool,
    reason: str,
    worker_endpoint_id: str,
    worker_kind: str,
    execution_program: str,
    checks: list[dict[str, Any]],
    capabilities: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": PROBE_SCHEMA_VERSION,
        "ready": ready,
        "reason": reason,
        "checks": checks,
        "capability": {
            "schema_version": "loopx_worker_capability_v0",
            "worker_endpoint_id": worker_endpoint_id,
            "worker_kind": worker_kind,
            "execution_protocol": "loopx_turn_v0",
            "session_ownership": "managed_runtime",
            "execution_program": execution_program,
            "capabilities": dict(capabilities),
            "availability": {"ready": ready, "reason": reason},
        },
    }


def probe_codex_cli(
    *,
    command: Sequence[str],
    workspace: Path,
    worker_endpoint_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Admit a Codex CLI Coding Worker when the binary answers ``--version``."""

    environ = dict(environment if environment is not None else os.environ)
    binary = command[0] if command else "codex"
    resolved = shutil.which(binary, path=environ.get("PATH"))
    checks: list[dict[str, Any]] = []
    if resolved is None:
        checks.append(
            {
                "name": "binary",
                "ok": False,
                "required": True,
                "detail": "codex binary not found on PATH",
            }
        )
        return _base_result(
            ready=False,
            reason="binary_not_installed",
            worker_endpoint_id=worker_endpoint_id,
            worker_kind="codex_cli_coding_worker",
            execution_program="codex_cli_headless",
            checks=checks,
            capabilities={},
        )
    checks.append(
        {
            "name": "binary",
            "ok": True,
            "required": True,
            "detail": "codex binary resolved",
        }
    )
    version_ok, version_detail = _run_version(
        [resolved, "--version"], env=environ, cwd=workspace
    )
    checks.append(
        {
            "name": "version",
            "ok": version_ok,
            "required": True,
            "detail": version_detail,
        }
    )
    ready = version_ok
    return _base_result(
        ready=ready,
        reason="ready" if ready else f"version: {version_detail}",
        worker_endpoint_id=worker_endpoint_id,
        worker_kind="codex_cli_coding_worker",
        execution_program="codex_cli_headless",
        checks=checks,
        capabilities={
            "shell": "probe_required",
            "file_read": "probe_required",
            "workspace_write": "probe_required",
            "git": "probe_required",
            "session_resume": True,
            "structured_events": True,
            "model_selection": True,
        },
    )


def probe_cursor_cli(
    *,
    command: Sequence[str],
    workspace: Path,
    worker_endpoint_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Admit a Cursor CLI Coding Worker after probing ``agent`` / ``cursor-agent``.

    The official docs currently use ``agent``; older installs may still expose
    ``cursor-agent``. Admission accepts the first name that resolves and answers
    a version/help probe.
    """

    environ = dict(environment if environment is not None else os.environ)
    candidates = list(command) if command else ["agent", "cursor-agent"]
    checks: list[dict[str, Any]] = []
    resolved: str | None = None
    selected_name = ""
    for name in candidates:
        found = shutil.which(str(name), path=environ.get("PATH"))
        if found is not None:
            resolved = found
            selected_name = str(name)
            break
    if resolved is None:
        checks.append(
            {
                "name": "binary",
                "ok": False,
                "required": True,
                "detail": "neither agent nor cursor-agent found on PATH",
            }
        )
        return _base_result(
            ready=False,
            reason="binary_not_installed",
            worker_endpoint_id=worker_endpoint_id,
            worker_kind="cursor_cli_coding_worker",
            execution_program="cursor_cli_generic",
            checks=checks,
            capabilities={},
        )
    checks.append(
        {
            "name": "binary",
            "ok": True,
            "required": True,
            "detail": f"resolved as {selected_name}",
        }
    )
    version_ok, version_detail = _run_version(
        [resolved, "--version"], env=environ, cwd=workspace
    )
    if not version_ok:
        # Some builds only expose help; treat a successful help exit as installed.
        help_ok, help_detail = _run_version(
            [resolved, "--help"], env=environ, cwd=workspace
        )
        version_ok = help_ok
        version_detail = help_detail if help_ok else version_detail
    checks.append(
        {
            "name": "version",
            "ok": version_ok,
            "required": True,
            "detail": version_detail,
        }
    )
    ready = version_ok
    return _base_result(
        ready=ready,
        reason="ready" if ready else f"version: {version_detail}",
        worker_endpoint_id=worker_endpoint_id,
        worker_kind="cursor_cli_coding_worker",
        execution_program="cursor_cli_generic",
        checks=checks,
        capabilities={
            "shell": "probe_required",
            "file_read": "probe_required",
            "workspace_write": "probe_required",
            "git": "probe_required",
            "session_resume": True,
            "structured_events": True,
            "model_selection": True,
        },
    )

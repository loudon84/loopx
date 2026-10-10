"""Runtime availability probe for the DSH 0.2 CLI coding worker.

The probe answers one question: *can this host actually execute a governed Turn
right now?* It never upgrades an unmeasured capability, and it reports the
first failing check as a typed reason instead of a bare boolean.

Every check here was derived from measured behaviour of the installed
``dsh 0.2.0-rc.2`` (see ``docs/implementation/dsh-coding-worker-phase0.md``).
The ``dsh_home_writable`` check is not optional: the launcher rewrites
``$DSH_HOME/profiles/<name>/cordis.yml`` on *every* invocation, including
``--help``, so a read-only home fails every Turn at boot.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loopx.dsh_goal_mode.dsh_cli_process import (
    DshCliInvocation,
    DshCliProcessError,
    run_invocation,
)

CAPABILITY_SCHEMA_VERSION = "loopx_dsh_coding_worker_capability_v1"
PROBE_SCHEMA_VERSION = "loopx_dsh_coding_worker_probe_v1"

#: Exactly the launcher release this adapter was measured against.
MEASURED_DSH_VERSION = "0.2.0-rc.2"
#: Major/minor line the adapter accepts; a moved line needs re-measurement.
SUPPORTED_DSH_VERSION_PREFIX = "0.2."

#: Headless composition rows that must be present for coding work.
#: Measured from `dsh --profile headless --dump-config` on 0.2.0-rc.2.
REQUIRED_HEADLESS_ROWS = ("tool-bash", "tool-fs")
#: Rows checked but only advisory: they gate specific capabilities.
ADVISORY_HEADLESS_ROWS = {
    "tool-fs-search": "file_search",
    "tool-skill": "skills",
    "tool-web": "web",
}

_PROBE_TIMEOUT_SECONDS = 60.0


@dataclass
class ProbeCheck:
    name: str
    ok: bool
    detail: str
    required: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "required": self.required,
            "detail": self.detail,
        }


@dataclass
class DshCliRuntime:
    """Resolved runtime identity for one worker instance."""

    dsh_bin: str = "dsh"
    dsh_home: Path | None = None
    workspace: Path | None = None
    provider: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    max_tokens: int | None = None
    timeout_seconds: float | None = None
    extra_env: dict[str, str] = field(default_factory=dict)


def _run_probe_command(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float = _PROBE_TIMEOUT_SECONDS,
) -> tuple[int | None, str, str]:
    """Run one read-only probe command through the same supervisor."""

    try:
        outcome = run_invocation(
            DshCliInvocation(
                argv=argv,
                cwd=cwd,
                stdin_text="",
                env=env,
                wall_timeout_seconds=timeout,
                idle_timeout_seconds=timeout,
            )
        )
    except DshCliProcessError as exc:
        return None, "", str(exc)
    return outcome.returncode, outcome.stdout, outcome.stderr


def _check_binary(runtime: DshCliRuntime, env: dict[str, str]) -> ProbeCheck:
    resolved = shutil.which(runtime.dsh_bin, path=env.get("PATH"))
    if resolved is None:
        return ProbeCheck(
            "binary_present", False, f"dsh binary {runtime.dsh_bin!r} is not on PATH"
        )
    return ProbeCheck("binary_present", True, resolved)


def _check_version(
    runtime: DshCliRuntime, env: dict[str, str], workspace: Path
) -> tuple[ProbeCheck, str]:
    code, out, err = _run_probe_command(
        (runtime.dsh_bin, "--version"), cwd=workspace, env=env
    )
    text = (out or err).strip()
    if code != 0:
        return ProbeCheck("version", False, f"`dsh --version` exited {code}"), ""
    if not text.startswith(SUPPORTED_DSH_VERSION_PREFIX):
        return (
            ProbeCheck(
                "version",
                False,
                f"dsh {text!r} is outside the measured {SUPPORTED_DSH_VERSION_PREFIX}x line",
            ),
            text,
        )
    if text != MEASURED_DSH_VERSION:
        return (
            ProbeCheck(
                "version",
                True,
                f"dsh {text!r} is on the supported line but was not the measured "
                f"{MEASURED_DSH_VERSION}; re-measure before claiming its event schema",
            ),
            text,
        )
    return ProbeCheck("version", True, text), text


def _check_headless_profile(
    runtime: DshCliRuntime, env: dict[str, str], workspace: Path
) -> tuple[ProbeCheck, set[str]]:
    code, out, err = _run_probe_command(
        (runtime.dsh_bin, "--profile", "headless", "--help"), cwd=workspace, env=env
    )
    text = f"{out}\n{err}"
    if code != 0:
        first = text.strip().splitlines()[0] if text.strip() else "no output"
        return (
            ProbeCheck(
                "headless_profile",
                False,
                f"`dsh --profile headless --help` exited {code}: {first[:300]}",
            ),
            set(),
        )
    missing = [flag for flag in ("--json", "--session-id") if flag not in text]
    if missing:
        return (
            ProbeCheck(
                "headless_profile",
                False,
                f"headless help omits required option(s): {', '.join(missing)}",
            ),
            set(),
        )
    return ProbeCheck("headless_profile", True, "--json and --session-id present"), set()


def _check_dsh_home_writable(runtime: DshCliRuntime) -> ProbeCheck:
    """The launcher rewrites ``profiles/<name>/cordis.yml`` on every boot."""

    home = runtime.dsh_home
    if home is None:
        return ProbeCheck(
            "dsh_home_writable",
            False,
            "no DSH_HOME resolved; the launcher needs a writable home",
        )
    target = home / "profiles"
    try:
        target.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", dir=target, prefix=".loopx-probe-", suffix=".tmp", delete=True
        ) as handle:
            handle.write("probe")
    except OSError as exc:
        return ProbeCheck(
            "dsh_home_writable",
            False,
            f"DSH_HOME {home} is not writable ({exc.strerror or exc}); "
            "every dsh boot would fail while rewriting cordis.yml",
        )
    return ProbeCheck("dsh_home_writable", True, str(home))


def _check_credentials(runtime: DshCliRuntime, env: dict[str, str]) -> ProbeCheck:
    if env.get("DEEPSEEK_API_KEY", "").strip():
        return ProbeCheck("credentials", True, "DEEPSEEK_API_KEY present in the host env")
    home = runtime.dsh_home
    if home is not None:
        for candidate in (home / ".credentials.yaml", home / "credentials.yaml"):
            if candidate.is_file():
                return ProbeCheck("credentials", True, "credential store present")
    return ProbeCheck(
        "credentials",
        False,
        "no DEEPSEEK_API_KEY and no DSH_HOME credential store; a real Turn would fail closed",
    )


def _check_headless_rows(
    runtime: DshCliRuntime, env: dict[str, str], workspace: Path
) -> tuple[ProbeCheck, dict[str, bool]]:
    code, out, err = _run_probe_command(
        (runtime.dsh_bin, "--profile", "headless", "--dump-config"),
        cwd=workspace,
        env=env,
    )
    if code != 0:
        first = (err or out).strip().splitlines()
        return (
            ProbeCheck(
                "headless_composition",
                False,
                f"`--dump-config` exited {code}: {first[0][:200] if first else 'no output'}",
            ),
            {},
        )
    rows = {
        line.split("id:", 1)[1].strip()
        for line in out.splitlines()
        if line.lstrip().startswith("- id:")
    }
    missing = [row for row in REQUIRED_HEADLESS_ROWS if row not in rows]
    capabilities = {
        "shell": any(r.startswith("tool-bash") for r in rows),
        "file_read": any(r.startswith("tool-fs") for r in rows),
        "workspace_write": any("sandbox" in r or r.startswith("tool-fs") for r in rows),
        "git": any(r.startswith("tool-bash") for r in rows),
    }
    if missing:
        return (
            ProbeCheck(
                "headless_composition",
                False,
                f"headless composition omits required row(s): {', '.join(sorted(missing))}",
            ),
            capabilities,
        )
    return (
        ProbeCheck(
            "headless_composition",
            True,
            f"{len(rows)} composed rows include the coding tool set",
        ),
        capabilities,
    )


def probe_dsh_cli(runtime: DshCliRuntime) -> dict[str, Any]:
    """Probe one worker instance and return its capability document."""

    workspace = (runtime.workspace or Path.cwd()).expanduser().resolve()
    env = dict(os.environ)
    if runtime.dsh_home is not None:
        env["DSH_HOME"] = str(runtime.dsh_home)
    env.update({str(k): str(v) for k, v in (runtime.extra_env or {}).items()})

    checks: list[ProbeCheck] = []
    binary = _check_binary(runtime, env)
    checks.append(binary)

    version_check = ProbeCheck("version", False, "skipped: dsh binary unavailable")
    profile_check = ProbeCheck("headless_profile", False, "skipped")
    rows_check = ProbeCheck("headless_composition", False, "skipped")
    capabilities: dict[str, bool] = {}

    if binary.ok:
        version_check, _ = _check_version(runtime, env, workspace)
        checks.append(version_check)
        if version_check.ok:
            profile_check, _ = _check_headless_profile(runtime, env, workspace)
            checks.append(profile_check)
            if profile_check.ok:
                rows_check, capabilities = _check_headless_rows(runtime, env, workspace)
                checks.append(rows_check)

    home_check = _check_dsh_home_writable(runtime)
    checks.append(home_check)
    credential_check = _check_credentials(runtime, env)
    checks.append(credential_check)

    required_failures = [c for c in checks if c.required and not c.ok]
    ready = not required_failures
    reason = (
        "ready"
        if ready
        else f"{required_failures[0].name}: {required_failures[0].detail}"
    )

    return {
        "schema_version": PROBE_SCHEMA_VERSION,
        "ready": ready,
        "reason": reason,
        "workspace": str(workspace),
        "dsh_bin": binary.detail if binary.ok else runtime.dsh_bin,
        "measured_version": MEASURED_DSH_VERSION,
        "checks": [check.as_dict() for check in checks],
        "capability": {
            "schema_version": CAPABILITY_SCHEMA_VERSION,
            "worker_endpoint_id": "dsh-coding-worker-local",
            "worker_kind": "dsh_coding_worker",
            "execution_protocol": "loopx_turn_v0",
            "session_ownership": "managed_runtime",
            "execution_program": "dsh_cli_headless",
            "capabilities": {
                # Measured from the headless composition, not assumed.
                "shell": capabilities.get("shell", False),
                "file_read": capabilities.get("file_read", False),
                "workspace_write": capabilities.get("workspace_write", False),
                "git": capabilities.get("git", False),
                # Not measured by this probe; stays unclaimed until a second
                # real cross-process run proves adoption (phase0 U-01).
                "session_resume": "probe_required",
                "structured_events": (
                    True if profile_check.ok and version_check.ok else "probe_required"
                ),
            },
            "availability": {"ready": ready, "reason": reason},
        },
    }

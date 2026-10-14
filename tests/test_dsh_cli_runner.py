"""Contract and unit tests for the DSH 0.2 CLI governed-Turn host.

Two levels are covered:

* **unit** — ``run_dsh_turn`` against a hermetic fake launcher: stdin delivery,
  argv shape, completion rule, failure closure, workspace/session identity;
* **contract** — LoopX's own ``turn_host_adapter.run_dsh_host`` driving this
  module as its ``dsh_runner``, proving the CLI host is a drop-in for the SDK
  host on the existing ``loopx_turn_host_request_v0`` boundary.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from hashlib import sha256
from pathlib import Path

import pytest

from loopx import dsh_goal_mode
from loopx.control_plane.quota.turn_envelope import (
    turn_envelope_action_signature_document,
)
from loopx.control_plane.turn_driver.host_failure import BuiltInHostError
from loopx.dsh_goal_mode import dsh_cli_runner, turn_host_adapter
from tests.dsh_cli_fake_launcher import write_fake_launcher

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_RUNNER_PATH = REPO_ROOT / "loopx" / "dsh_goal_mode" / "dsh_cli_runner.py"
TURN_KEY = "sha256:" + "0" * 64


def _signed_request(
    *,
    primary_action: str = "Do the signed thing.",
    goal_id: str = "g",
    agent_id: str = "a",
    todo_id: str | None = None,
) -> dict:
    request = {
        "schema_version": dsh_goal_mode.LOOPX_TURN_HOST_REQUEST_SCHEMA,
        "turn_key": TURN_KEY,
        "route": "primary_delivery",
        "session": {"goal_id": goal_id, "agent_id": agent_id},
        "turn_envelope": {
            "schema_version": "loopx_turn_envelope_v0",
            "goal_id": goal_id,
            "agent_id": agent_id,
            "action": {
                "recommended_action": "legacy action must not win",
                "primary_action": primary_action,
                "must_attempt": True,
            },
            "required_reads": [],
            "boundary": {
                "write_scope": ["docs/**", "tests/**", "loopx/**"],
                "workspace_guard": {
                    "schema_version": "workspace_guard_v0",
                    "status": "ready",
                    "action": "continue",
                },
            },
            "action_signature": {
                "schema_version": "loopx_action_signature_v0",
                "matches": True,
            },
        },
        "result_contract": {
            "schema_version": dsh_goal_mode.LOOPX_TURN_RESULT_SCHEMA,
            "completed_phases": list(dsh_goal_mode.COMPLETED_PHASES),
        },
    }
    if todo_id is not None:
        request["turn_envelope"]["action"]["selected_todo"] = {"todo_id": todo_id}
    signature = turn_envelope_action_signature_document(request["turn_envelope"])
    signature_hash = "sha256:" + sha256(
        json.dumps(
            signature, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    request["turn_envelope"]["action_signature"].update(
        {"source_hash": signature_hash, "envelope_hash": signature_hash}
    )
    return request


@pytest.fixture()
def launcher(tmp_path: Path) -> Path:
    return write_fake_launcher(tmp_path)


def _run(
    launcher: Path,
    tmp_path: Path,
    *,
    mode: str,
    prompt: str = "do the work",
    session_id: str | None = None,
    workspace: Path | None = None,
    timeout: float | None = 60.0,
) -> dict:
    record = tmp_path / "record.json"
    outcome = dsh_cli_runner.run_dsh_turn(
        prompt=prompt,
        session_id=session_id,
        workspace=workspace or tmp_path,
        session_root=tmp_path / "dsh-home",
        provider="deepseek",
        model="deepseek-flash",
        reasoning_effort="high",
        max_tokens=16384,
        cordis=None,
        runtime_bin=str(launcher),
        request_timeout_seconds=timeout,
        env={"FAKE_DSH_MODE": mode, "FAKE_DSH_RECORD": str(record)},
    )
    return {"outcome": outcome, "record": record}


def test_successful_turn_reports_completed_and_the_typed_result(
    launcher: Path, tmp_path: Path
) -> None:
    result = _run(launcher, tmp_path, mode="ok")
    outcome = result["outcome"]

    assert outcome["finish_reason"] == "completed"
    candidate = json.loads(outcome["final_response"])
    assert candidate["result_kind"] == "validated_progress"

    diagnostics = outcome["diagnostics"]
    assert diagnostics["exit_code"] == 0
    assert diagnostics["terminal_turn_kind"] == "completed"
    assert diagnostics["pid"] > 0
    assert diagnostics["duration_seconds"] >= 0
    assert diagnostics["session_mode"] == "fresh"
    assert diagnostics["reported_session_id"].startswith("session-fake")
    assert diagnostics["usage"]["totalTokens"] == 15


def test_prompt_travels_on_stdin_and_never_as_an_argv_word(
    launcher: Path, tmp_path: Path
) -> None:
    prompt = "PROMPT-CANARY --not-a-flag; rm -rf /"
    result = _run(launcher, tmp_path, mode="ok", prompt=prompt)

    record = json.loads(result["record"].read_text())

    assert record["stdin"] == prompt
    assert prompt not in record["argv"]
    assert not any("PROMPT-CANARY" in word for word in record["argv"])


def test_argv_uses_the_measured_headless_surface_only(
    launcher: Path, tmp_path: Path
) -> None:
    result = _run(launcher, tmp_path, mode="ok")

    argv = json.loads(result["record"].read_text())["argv"]

    assert argv[:3] == ["--profile", "headless", "--json"]
    assert argv[-1] == "-"
    # Only the documented 0.2 options may appear; no invented flags.
    allowed_with_value = {"--session-id", "--patch", "--profile"}
    index = 0
    while index < len(argv):
        word = argv[index]
        if word == "-":
            index += 1
            continue
        assert word in allowed_with_value | {"--json", "headless"}, word
        index += 2 if word in allowed_with_value else 1


def test_model_controls_are_applied_through_an_isolated_patch(
    launcher: Path, tmp_path: Path
) -> None:
    """Model/provider/effort enter argv via per-Turn ``--patch``, not shared profile."""

    result = _run(launcher, tmp_path, mode="ok")
    diagnostics = result["outcome"]["diagnostics"]
    argv = json.loads(result["record"].read_text())["argv"]

    assert "--patch" in argv
    patch = Path(argv[argv.index("--patch") + 1])
    assert patch.is_file()
    patch_text = patch.read_text(encoding="utf-8")
    assert "model: deepseek-flash" in patch_text
    assert "provider: deepseek" in patch_text
    assert "reasoningEffort: high" in patch_text
    assert diagnostics["applied_model_controls"]["model"] == "deepseek-flash"
    assert diagnostics["model_patch_applied"] is True
    # max_tokens remains unsupported on the measured CLI argv surface.
    assert diagnostics["unsupported_argv_controls"]["max_tokens"] == 16384
    assert "model" not in diagnostics["unsupported_argv_controls"]


def test_fresh_session_mode_does_not_pass_session_id(
    launcher: Path, tmp_path: Path
) -> None:
    result = _run(launcher, tmp_path, mode="ok", session_id="session-existing")

    argv = json.loads(result["record"].read_text())["argv"]

    assert "--session-id" not in argv
    assert result["outcome"]["diagnostics"]["session_mode"] == "fresh"


def test_resume_adopts_an_exact_persisted_session(
    launcher: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = "session-persisted"
    (tmp_path / "dsh-home" / "sessions" / "slug" / session_id).mkdir(parents=True)
    monkeypatch.setenv(dsh_cli_runner.SESSION_MODE_ENV, dsh_cli_runner.SESSION_MODE_RESUME)

    result = _run(launcher, tmp_path, mode="ok", session_id=session_id)

    argv = json.loads(result["record"].read_text())["argv"]
    assert argv[argv.index("--session-id") + 1] == session_id
    assert result["outcome"]["diagnostics"]["session_mode"] == "resume"


def test_resume_without_a_persisted_session_fails_closed(
    launcher: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(dsh_cli_runner.SESSION_MODE_ENV, dsh_cli_runner.SESSION_MODE_RESUME)

    with pytest.raises(dsh_cli_runner.DshCliTurnError) as excinfo:
        _run(launcher, tmp_path, mode="ok", session_id="session-absent")

    assert excinfo.value.code == "invalid_request"
    assert "session_missing" in str(excinfo.value)


def test_pre_turn_failure_on_nonzero_exit_is_not_a_success(
    launcher: Path, tmp_path: Path
) -> None:
    outcome = _run(launcher, tmp_path, mode="exit1_pre_turn")["outcome"]

    assert outcome["finish_reason"] == "error"
    assert outcome["diagnostics"]["exit_code"] == 1


def test_in_turn_error_reason_is_preserved(
    launcher: Path, tmp_path: Path
) -> None:
    outcome = _run(launcher, tmp_path, mode="in_turn_error")["outcome"]

    assert outcome["finish_reason"] == "error"
    reason = outcome["diagnostics"]["failure_reason"]
    assert reason["kind"] == "error"
    assert reason["error"]["code"] == "rate_limit"
    # The terminal marker LoopX reads must agree with finish_reason.
    assert outcome["events"][-1] == {"type": "turn/end", "data": {"reason": reason}}


def test_exit_zero_without_turn_end_is_not_a_success(
    launcher: Path, tmp_path: Path
) -> None:
    """Completion needs the terminal reason, not merely a clean exit."""

    outcome = _run(launcher, tmp_path, mode="no_turn_end")["outcome"]

    assert outcome["diagnostics"]["exit_code"] == 0
    assert outcome["finish_reason"] == "error"


def test_invalid_ndjson_fails_closed(launcher: Path, tmp_path: Path) -> None:
    with pytest.raises(dsh_cli_runner.DshCliTurnError) as excinfo:
        _run(launcher, tmp_path, mode="garbage")

    assert excinfo.value.code == "malformed_response"


def test_workspace_mismatch_is_rejected(
    launcher: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_DSH_CWD", "/somewhere/else")

    with pytest.raises(dsh_cli_runner.DshCliTurnError) as excinfo:
        _run(launcher, tmp_path, mode="ok")

    assert excinfo.value.code == "contract_rejected"


def test_wall_timeout_is_enforced(launcher: Path, tmp_path: Path) -> None:
    started = time.monotonic()
    with pytest.raises(dsh_cli_runner.DshCliTurnError) as excinfo:
        _run(launcher, tmp_path, mode="idle", timeout=2.0)

    assert excinfo.value.code == "timeout"
    assert time.monotonic() - started < 30


def test_empty_prompt_is_rejected(launcher: Path, tmp_path: Path) -> None:
    with pytest.raises(dsh_cli_runner.DshCliTurnError) as excinfo:
        dsh_cli_runner.run_dsh_turn(
            prompt="   ", workspace=tmp_path, runtime_bin=str(launcher)
        )

    assert excinfo.value.code == "invalid_request"


def test_missing_workspace_is_rejected(launcher: Path, tmp_path: Path) -> None:
    with pytest.raises(dsh_cli_runner.DshCliTurnError) as excinfo:
        dsh_cli_runner.run_dsh_turn(
            prompt="x", workspace=tmp_path / "nope", runtime_bin=str(launcher)
        )

    assert excinfo.value.code == "invalid_request"


# --------------------------------------------------------------------------
# Contract level: LoopX's own adapter drives this module as its dsh_runner.
# --------------------------------------------------------------------------


def test_loopx_adapter_accepts_the_cli_runner_as_a_drop_in(
    launcher: Path, tmp_path: Path
) -> None:
    config = turn_host_adapter.DshHostConfig(
        workspace=tmp_path,
        dsh_home=tmp_path / "dsh-home",
        runtime_bin=str(launcher),
        request_timeout_seconds=60.0,
        dsh_runner=CLI_RUNNER_PATH,
    )

    result = turn_host_adapter.run_dsh_host(
        _signed_request(primary_action="Do the CLI thing."), config=config
    )

    assert result["schema_version"] == dsh_goal_mode.LOOPX_TURN_RESULT_SCHEMA
    assert result["result_kind"] == "validated_progress"
    assert result["completed_phases"] == list(dsh_goal_mode.COMPLETED_PHASES)


def test_loopx_adapter_fails_closed_when_the_cli_turn_fails(
    launcher: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_DSH_MODE", "in_turn_error")
    config = turn_host_adapter.DshHostConfig(
        workspace=tmp_path,
        dsh_home=tmp_path / "dsh-home",
        runtime_bin=str(launcher),
        request_timeout_seconds=60.0,
        dsh_runner=CLI_RUNNER_PATH,
    )

    with pytest.raises(BuiltInHostError) as excinfo:
        turn_host_adapter.run_dsh_host(_signed_request(), config=config)

    assert excinfo.value.failure_kind == "rate_limited"


def test_loopx_adapter_rejects_an_unsigned_request(
    launcher: Path, tmp_path: Path
) -> None:
    request = _signed_request()
    request["turn_envelope"]["action_signature"]["matches"] = False
    config = turn_host_adapter.DshHostConfig(
        workspace=tmp_path,
        dsh_home=tmp_path / "dsh-home",
        runtime_bin=str(launcher),
        dsh_runner=CLI_RUNNER_PATH,
    )

    with pytest.raises(BuiltInHostError) as excinfo:
        turn_host_adapter.run_dsh_host(request, config=config)

    assert excinfo.value.failure_kind == "contract_rejected"


# --------------------------------------------------------------------------
# Supervisor level: bounds, cancellation and process-group cleanup.
# --------------------------------------------------------------------------


def test_cancel_stops_the_process_group(launcher: Path, tmp_path: Path) -> None:
    from loopx.dsh_goal_mode.dsh_cli_process import DshCliInvocation, run_invocation

    cancel = threading.Event()
    seen: dict[str, int] = {}

    def on_started(pid: int) -> None:
        seen["pid"] = pid
        cancel.set()

    outcome = run_invocation(
        DshCliInvocation(
            argv=(str(launcher), "--profile", "headless", "--json", "-"),
            cwd=tmp_path,
            stdin_text="task",
            env={"FAKE_DSH_MODE": "idle"},
            wall_timeout_seconds=30.0,
            idle_timeout_seconds=30.0,
        ),
        cancel=cancel,
        on_started=on_started,
    )

    assert outcome.cancelled is True
    assert outcome.timed_out is False
    # The child was reaped, not left as a zombie.
    with pytest.raises(ProcessLookupError):
        os.kill(seen["pid"], 0)


def test_bounded_capture_reports_truncation(launcher: Path, tmp_path: Path) -> None:
    from loopx.dsh_goal_mode.dsh_cli_process import DshCliInvocation, run_invocation

    outcome = run_invocation(
        DshCliInvocation(
            argv=(str(launcher), "--profile", "headless", "--json", "-"),
            cwd=tmp_path,
            stdin_text="task",
            env={"FAKE_DSH_MODE": "ok"},
            wall_timeout_seconds=30.0,
            idle_timeout_seconds=30.0,
            max_capture_bytes=40,
        )
    )

    assert outcome.stdout_truncated is True
    assert outcome.ok is False
    assert len(outcome.stdout.encode()) <= 40


def test_runner_loads_by_path_from_a_foreign_installation(
    launcher: Path, tmp_path: Path
) -> None:
    """The adapter loads this runner by *path*, not as an installed package.

    LoopX may be executing from a release snapshot while the runner is
    developed in another checkout. An absolute ``loopx.*`` import inside the
    runner then raises ``ModuleNotFoundError``, which ``load_dsh_runner``
    surfaces as a content-free ``unknown`` host failure and the Turn dies
    before the launcher ever starts. This test drives the runner from a
    subprocess whose ``sys.path`` contains no ``loopx`` package.
    """

    record = tmp_path / "record.json"
    script = textwrap.dedent(
        f"""
        import importlib.util, sys
        sys.path = [p for p in sys.path if "loopx" not in p]
        spec = importlib.util.spec_from_file_location(
            "dsh_turn_runner", {str(CLI_RUNNER_PATH)!r}
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["dsh_turn_runner"] = module
        spec.loader.exec_module(module)
        outcome = module.run_dsh_turn(
            prompt="do it",
            workspace={str(tmp_path)!r},
            session_root={str(tmp_path / "dsh-home")!r},
            runtime_bin={str(launcher)!r},
            request_timeout_seconds=60.0,
        )
        print(outcome["finish_reason"])
        """
    )
    process = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": "",
            "FAKE_DSH_MODE": "ok",
            "FAKE_DSH_RECORD": str(record),
        },
    )

    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == "completed"
    assert json.loads(record.read_text())["stdin"] == "do it"

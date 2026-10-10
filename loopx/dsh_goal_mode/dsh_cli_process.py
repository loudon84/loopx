"""Controlled subprocess supervision for one ``dsh`` headless Turn.

The launcher always starts ``dsh`` from an argv array with the task on stdin:
no shell string is ever composed, so a prompt can never become shell syntax.
The child is placed in its own process group so a stop request reaches every
descendant instead of orphaning them.

Three independent stop conditions are enforced, each reported as its own
failure so the Turn journal can classify the attempt:

* ``wall_timeout`` — the whole Turn exceeded its configured budget;
* ``idle_timeout`` — no stdout activity for the configured idle window (the
  measured stream has no pre-token heartbeat, so an idle bound is the only
  progress signal available);
* ``cancel`` — an owner asked to stop the run.

Capture is bounded: a runaway or hostile child cannot exhaust memory, and a
truncated capture is reported as such rather than silently parsed as a short
stream.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_WALL_TIMEOUT_SECONDS = 900.0
DEFAULT_IDLE_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_CAPTURE_BYTES = 8 * 1024 * 1024
_KILL_GRACE_SECONDS = 5.0


class DshCliProcessError(RuntimeError):
    """The supervised ``dsh`` process could not be started or reaped.

    ``code`` feeds LoopX's existing host-failure classifier
    (``loopx/dsh_goal_mode/host_failure_map.py``); it is deliberately drawn
    from that module's stable provider-code vocabulary.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DshCliInvocation:
    """One fully resolved ``dsh`` invocation."""

    argv: tuple[str, ...]
    cwd: Path
    stdin_text: str
    env: dict[str, str] | None = None
    wall_timeout_seconds: float | None = DEFAULT_WALL_TIMEOUT_SECONDS
    idle_timeout_seconds: float | None = DEFAULT_IDLE_TIMEOUT_SECONDS
    max_capture_bytes: int = DEFAULT_MAX_CAPTURE_BYTES


@dataclass
class ProcessOutcome:
    """The observed result of one supervised invocation."""

    returncode: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    pid: int | None = None
    timed_out: bool = False
    timeout_kind: str | None = None
    cancelled: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    stdout_event_count: int = 0
    first_event_seconds: float | None = None
    last_event_seconds: float | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return (
            self.returncode == 0
            and not self.timed_out
            and not self.cancelled
            and not self.stdout_truncated
        )


class _BoundedReader(threading.Thread):
    """Drain one pipe into a bounded buffer while tracking activity."""

    def __init__(self, stream: Any, limit: int, *, count_newlines: bool) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._limit = limit
        self._count_newlines = count_newlines
        self._chunks: list[bytes] = []
        self._size = 0
        self.truncated = False
        self.lines = 0
        self.last_activity = time.monotonic()

    def run(self) -> None:  # pragma: no cover - exercised through the supervisor
        try:
            while True:
                chunk = self._stream.read(8192)
                if not chunk:
                    break
                now = time.monotonic()
                if self._count_newlines:
                    self.lines += chunk.count(b"\n")
                self.last_activity = now
                room = self._limit - self._size
                if room > 0:
                    self._chunks.append(chunk[:room])
                    self._size += min(room, len(chunk))
                if len(chunk) > max(room, 0):
                    # Keep draining so the child never blocks on a full pipe,
                    # but stop retaining; the caller sees `truncated`.
                    self.truncated = True
        except (OSError, ValueError):
            return
        finally:
            try:
                self._stream.close()
            except (OSError, ValueError):
                pass

    @property
    def text(self) -> str:
        return b"".join(self._chunks).decode("utf-8", errors="replace")


def terminate_process_group(pid: int, *, grace_seconds: float = _KILL_GRACE_SECONDS) -> None:
    """Terminate a whole process group, escalating to ``SIGKILL``.

    The call is idempotent and never raises: it is used on timeout, on
    cancellation, and during restart reconciliation.
    """

    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, PermissionError, OSError):
        return
    for sig, wait in ((signal.SIGTERM, grace_seconds), (signal.SIGKILL, 0.2)):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            return
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            try:
                os.killpg(pgid, 0)
            except (ProcessLookupError, OSError):
                return
            time.sleep(0.05)


def run_invocation(
    invocation: DshCliInvocation,
    *,
    cancel: threading.Event | None = None,
    on_started: Any = None,
) -> ProcessOutcome:
    """Run one invocation under wall, idle and cancellation bounds."""

    if not invocation.argv:
        raise DshCliProcessError("invalid_request", "empty argv for dsh invocation")

    environment = dict(os.environ)
    if invocation.env:
        environment.update({str(k): str(v) for k, v in invocation.env.items()})

    started = time.monotonic()
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            list(invocation.argv),
            cwd=str(invocation.cwd),
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise DshCliProcessError(
            "missing_credential", f"dsh binary not found: {invocation.argv[0]}"
        ) from exc
    except (OSError, ValueError) as exc:
        raise DshCliProcessError("transport", f"cannot start dsh: {exc}") from exc

    if on_started is not None:
        try:
            on_started(process.pid)
        except Exception:  # noqa: BLE001 - readback must not break the run
            pass

    stdout_reader = _BoundedReader(
        process.stdout, invocation.max_capture_bytes, count_newlines=True
    )
    stderr_reader = _BoundedReader(
        process.stderr, invocation.max_capture_bytes, count_newlines=False
    )
    stdout_reader.start()
    stderr_reader.start()

    try:
        if process.stdin is not None:
            try:
                process.stdin.write(invocation.stdin_text.encode("utf-8"))
            except (BrokenPipeError, OSError):
                # The child may exit before reading stdin; that is reported
                # through the exit status and stderr, not as a supervisor bug.
                pass
            finally:
                try:
                    process.stdin.close()
                except (BrokenPipeError, OSError):
                    pass

        timeout_kind: str | None = None
        returncode: int | None = None
        seen_events = 0
        first_event: float | None = None

        while True:
            if cancel is not None and cancel.is_set():
                timeout_kind = "cancel"
                terminate_process_group(process.pid)
                break
            returncode = process.poll()
            if returncode is not None:
                break

            now = time.monotonic()
            if (
                invocation.wall_timeout_seconds is not None
                and now - started > invocation.wall_timeout_seconds
            ):
                timeout_kind = "wall_timeout"
                terminate_process_group(process.pid)
                break
            if (
                invocation.idle_timeout_seconds is not None
                and now - stdout_reader.last_activity > invocation.idle_timeout_seconds
            ):
                timeout_kind = "idle_timeout"
                terminate_process_group(process.pid)
                break

            # Track first-observed event for the real latency readback.
            if stdout_reader.lines > seen_events:
                seen_events = stdout_reader.lines
                if first_event is None:
                    first_event = time.monotonic() - started
            time.sleep(0.05)

        try:
            returncode = process.wait(timeout=_KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            terminate_process_group(process.pid)
            try:
                returncode = process.wait(timeout=_KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:  # pragma: no cover - kernel straggler
                returncode = None
    finally:
        stdout_reader.join(timeout=10.0)
        stderr_reader.join(timeout=10.0)
        if process.poll() is None:  # pragma: no cover - defensive reap
            terminate_process_group(process.pid)

    duration = time.monotonic() - started
    return ProcessOutcome(
        returncode=returncode,
        stdout=stdout_reader.text,
        stderr=stderr_reader.text,
        duration_seconds=duration,
        pid=process.pid,
        timed_out=timeout_kind in {"wall_timeout", "idle_timeout"},
        timeout_kind=timeout_kind,
        cancelled=timeout_kind == "cancel",
        stdout_truncated=stdout_reader.truncated,
        stderr_truncated=stderr_reader.truncated,
        stdout_event_count=stdout_reader.lines,
        first_event_seconds=first_event,
        last_event_seconds=(
            stdout_reader.last_activity - started if stdout_reader.lines else None
        ),
    )

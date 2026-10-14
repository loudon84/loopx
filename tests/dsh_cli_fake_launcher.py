"""Hermetic fake ``dsh`` launcher for the CLI host tests.

Writes an executable Python script that mimics the measured argv/stdout
behaviour of ``dsh --profile headless --json`` without a model, a network or a
real DeepSeek Harness install. Not a pytest module (no ``test_`` prefix).

The fake records the argv it was handed and the stdin it received so a test can
assert the adapter passed the task on stdin rather than as an argv word.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

FAKE_LAUNCHER_SOURCE = '''#!/usr/bin/env python3
"""Fake dsh launcher for hermetic tests. Not a production artifact."""
import json
import os
import sys
import time

argv = sys.argv[1:]
mode = os.environ.get("FAKE_DSH_MODE", "ok")
record = os.environ.get("FAKE_DSH_RECORD")

if argv[:1] == ["--version"]:
    print(os.environ.get("FAKE_DSH_VERSION", "0.2.0-rc.2"))
    raise SystemExit(0)

if "--help" in argv:
    print("Usage: dsh --profile headless [options] [task...]")
    print("  --json             write newline-delimited run events to stdout")
    print("  --session-id <id>  adopt the persisted Session with this id")
    print("  -h, --help         show this help")
    raise SystemExit(0)

if "--dump-config" in argv:
    for row in ("tool-bash", "tool-fs", "tool-fs-search", "tool-skill", "sandbox"):
        print("- id: %s" % row)
    raise SystemExit(0)

session_id = None
if "--session-id" in argv:
    session_id = argv[argv.index("--session-id") + 1]

stdin_text = sys.stdin.read() if not sys.stdin.isatty() else ""
cwd = os.environ.get("FAKE_DSH_CWD", os.getcwd())

if record:
    with open(record, "w", encoding="utf-8") as handle:
        json.dump({"argv": argv, "stdin": stdin_text, "cwd": cwd}, handle)

session = session_id or os.environ.get("FAKE_DSH_SESSION", "session-fake-0001")
RESULT = json.dumps({
    "result_kind": "validated_progress",
    "classification": "fake progress",
    "summary": "the fake launcher reported progress",
    "recommended_action": "continue with the next todo",
    "next_action": "continue with the next todo",
    "vision_unchanged_reason": "no goal path change",
})

def emit(event):
    sys.stdout.write(json.dumps(event) + "\\n")
    sys.stdout.flush()

if mode == "garbage":
    print("this is not json")
    raise SystemExit(0)

emit({"type": "session", "sessionId": session, "cwd": cwd})

if mode == "idle":
    time.sleep(120)
    raise SystemExit(0)

if mode == "exit1_pre_turn":
    emit({"type": "error", "message": "session \\"%s\\" does not exist" % session})
    raise SystemExit(1)

emit({"type": "status", "phase": "turn_start", "turn": 1})

if mode == "in_turn_error":
    emit({"type": "status", "phase": "turn_end", "turn": 1,
          "reason": {"kind": "error", "error": {"code": "rate_limit", "message": "slow down"}}})
    emit({"type": "final", "text": ""})
    raise SystemExit(1)

if mode == "no_turn_end":
    emit({"type": "text", "text": "partial"})
    raise SystemExit(0)

emit({"type": "tool_call", "callId": "c1", "tool": "bash",
      "input": {"command": "echo hi"}})
emit({"type": "tool_result", "callId": "c1", "status": "completed", "result": "hi\\n"})
emit({"type": "status", "phase": "step_end", "turn": 1, "step": 1,
      "usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15}})
emit({"type": "status", "phase": "turn_end", "turn": 1, "reason": {"kind": "completed"}})
emit({"type": "final", "text": RESULT})
raise SystemExit(0)
'''


def write_fake_launcher(directory: Path, *, name: str = "fake-dsh") -> Path:
    """Write the executable fake launcher into ``directory`` and return its path.

    On Windows the shebang script is not a valid Win32 image, so the helper also
    writes a ``.cmd`` wrapper that invokes the same source via the current
    Python and returns that wrapper path for ``LOOPX_DSH_CODING_WORKER_BIN``.
    """

    script = directory / f"{name}.py"
    script.write_text(FAKE_LAUNCHER_SOURCE, encoding="utf-8")
    script.chmod(0o755)
    if os.name != "nt":
        # POSIX keeps the historical extension-less name as a symlink/copy.
        target = directory / name
        if target.exists() or target.is_symlink():
            target.unlink()
        try:
            target.hardlink_to(script)
        except OSError:
            target.write_text(FAKE_LAUNCHER_SOURCE, encoding="utf-8")
            target.chmod(0o755)
        return target
    wrapper = directory / f"{name}.cmd"
    wrapper.write_text(
        "@echo off\r\n"
        f'"{sys.executable}" "%~dp0{name}.py" %*\r\n',
        encoding="utf-8",
    )
    return wrapper

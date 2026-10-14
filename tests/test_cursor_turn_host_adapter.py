"""Unit tests for the Cursor CLI governed-Turn adapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.control_plane.turn_driver.host_candidate import LOOPX_TURN_RESULT_SCHEMA
from loopx.cursor_worker import turn_host_adapter as adapter


def test_extract_candidate_from_result_event() -> None:
    stdout = "\n".join(
        [
            json.dumps({"type": "assistant", "text": "working"}),
            json.dumps(
                {
                    "type": "result",
                    "result_kind": "validated_progress",
                    "classification": "ok",
                    "summary": "done",
                    "recommended_action": "continue",
                    "next_action": "next",
                    "vision_unchanged_reason": "path unchanged",
                }
            ),
        ]
    )
    candidate = adapter.extract_candidate_from_stream(stdout)
    assert candidate is not None
    assert candidate["result_kind"] == "validated_progress"


def test_extract_candidate_from_embedded_json() -> None:
    stdout = json.dumps(
        {
            "type": "assistant",
            "text": 'here is the result {"result_kind":"wait","classification":"blocked","summary":"need user","recommended_action":"ask","next_action":"ask","vision_unchanged_reason":"none"}',
        }
    )
    candidate = adapter.extract_candidate_from_stream(stdout)
    assert candidate is not None
    assert candidate["result_kind"] == "wait"


def test_bad_json_and_missing_result_return_none() -> None:
    assert adapter.extract_candidate_from_stream("{not json\n") is None
    assert (
        adapter.extract_candidate_from_stream(
            json.dumps({"type": "assistant", "text": "no structured result"})
        )
        is None
    )


def test_build_cursor_argv_requires_model_and_avoids_yolo(tmp_path: Path) -> None:
    argv = adapter.build_cursor_argv(
        binary="agent",
        workspace=tmp_path,
        model="composer-2",
        session_id=None,
        prompt="do work",
    )
    assert "--print" in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert argv[argv.index("--model") + 1] == "composer-2"
    assert "--force" not in argv
    assert "--yolo" not in argv
    assert "--resume" not in argv


def test_explicit_session_resume_is_opt_in(tmp_path: Path) -> None:
    argv = adapter.build_cursor_argv(
        binary="agent",
        workspace=tmp_path,
        model="composer-2",
        session_id="sess-123",
        prompt="do work",
    )
    assert argv[argv.index("--resume") + 1] == "sess-123"


def test_missing_model_is_rejected() -> None:
    with pytest.raises(ValueError, match="explicit model"):
        adapter._request_model({"execution_package": {}}, None)


def test_build_result_fail_closed_wait() -> None:
    request = {
        "schema_version": "loopx_turn_host_request_v0",
        "turn_key": "sha256:" + "a" * 64,
    }
    result = adapter.build_host_result(
        request,
        None,
        host_name="Cursor CLI",
        fallback_reason="missing result",
    )
    assert result["schema_version"] == LOOPX_TURN_RESULT_SCHEMA
    assert result["result_kind"] == "wait"

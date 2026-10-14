"""Keyless unit tests for the DSH 0.2 CLI NDJSON codec.

The ``REAL_PROBE_STREAM`` fixture is the verbatim stdout of one real
``dsh --profile headless --json -`` run measured against the installed
``0.2.0-rc.2`` package (Phase 0 evidence). It is the regression anchor for the
event vocabulary: if a launcher upgrade changes the stream shape, this test
should be the first thing that fails.
"""

from __future__ import annotations

import json

import pytest

from loopx.dsh_goal_mode import dsh_cli_ndjson as codec

REAL_PROBE_STREAM = "\n".join(
    [
        '{"type":"session","sessionId":"session-eb12661f-0101-4945-be3f-9a3d6707dab2","cwd":"/w"}',
        '{"type":"status","phase":"turn_start","turn":1}',
        '{"type":"status","phase":"step_start","turn":1,"step":1}',
        '{"type":"thinking","text":"run it"}',
        '{"type":"tool_call","callId":"call_1","tool":"bash","input":{"command":"echo LOOPX-PROBE-OK"}}',
        '{"type":"tool_result","callId":"call_1","status":"completed","result":"LOOPX-PROBE-OK\\n"}',
        '{"type":"status","phase":"step_end","turn":1,"step":1,"usage":{"inputTokens":7501,"outputTokens":95,"cacheReadTokens":0,"cacheWriteTokens":0,"totalTokens":7596}}',
        '{"type":"status","phase":"step_start","turn":1,"step":2}',
        '{"type":"thinking","text":""}',
        '{"type":"text","text":"LOOPX-PROBE-OK"}',
        '{"type":"status","phase":"step_end","turn":1,"step":2,"usage":{"inputTokens":193,"outputTokens":9,"cacheReadTokens":7424,"cacheWriteTokens":0,"totalTokens":7626}}',
        '{"type":"status","phase":"turn_end","turn":1,"reason":{"kind":"completed"}}',
        '{"type":"final","text":"LOOPX-PROBE-OK"}',
    ]
)


def test_real_probe_stream_parses_into_the_measured_vocabulary() -> None:
    events = codec.parse_event_stream(REAL_PROBE_STREAM)

    assert [event["type"] for event in events] == [
        "session",
        "status",
        "status",
        "thinking",
        "tool_call",
        "tool_result",
        "status",
        "status",
        "thinking",
        "text",
        "status",
        "status",
        "final",
    ]
    assert {event["type"] for event in events} <= codec.CLI_EVENT_TYPES


def test_real_probe_stream_reports_identity_terminal_and_usage() -> None:
    events = codec.parse_event_stream(REAL_PROBE_STREAM)

    assert codec.session_identity(events) == {
        "session_id": "session-eb12661f-0101-4945-be3f-9a3d6707dab2",
        "cwd": "/w",
    }
    assert codec.terminal_turn_kind(events) == codec.COMPLETED_TURN_KIND
    assert codec.terminal_turn_reason(events) == {"kind": "completed"}
    assert codec.final_text(events) == "LOOPX-PROBE-OK"
    assert codec.error_events(events) == []
    assert codec.accumulate_usage(events) == {
        "inputTokens": 7694,
        "outputTokens": 104,
        "cacheReadTokens": 7424,
        "cacheWriteTokens": 0,
        "totalTokens": 15222,
    }


def test_tool_calls_pair_with_results_by_call_id() -> None:
    events = codec.parse_event_stream(REAL_PROBE_STREAM)

    summaries = codec.tool_summaries(events)

    assert len(summaries) == 1
    assert summaries[0]["tool"] == "bash"
    assert summaries[0]["status"] == "completed"
    assert "LOOPX-PROBE-OK" in summaries[0]["result"]


def test_unmatched_tool_call_is_reported_not_hidden() -> None:
    events = codec.parse_event_stream(
        '{"type":"tool_call","callId":"c9","tool":"bash","input":{}}'
    )

    assert codec.tool_summaries(events)[0]["status"] == "unmatched"


@pytest.mark.parametrize(
    "line",
    [
        "not json at all",
        "[1,2,3]",
        '{"no":"type"}',
        '{"type":""}',
    ],
)
def test_malformed_event_lines_fail_closed(line: str) -> None:
    with pytest.raises(codec.DshCliProtocolError):
        codec.parse_event_line(line)


def test_blank_lines_are_ignored() -> None:
    assert codec.parse_event_line("   ") is None
    assert codec.parse_event_stream("\n\n") == []


def test_missing_terminal_reason_is_none_not_completed() -> None:
    """A truncated stream must not look like a completed Turn."""

    events = codec.parse_event_stream(
        '{"type":"session","sessionId":"s","cwd":"/w"}\n'
        '{"type":"status","phase":"turn_start","turn":1}'
    )

    assert codec.terminal_turn_kind(events) is None
    assert codec.final_text(events) == ""


def test_in_turn_failure_keeps_final_without_error() -> None:
    """Measured: an in-turn failure ends with `final` and no `error` event."""

    events = codec.parse_event_stream(
        '{"type":"status","phase":"turn_end","turn":1,"reason":{"kind":"error"}}\n'
        '{"type":"final","text":""}'
    )

    assert codec.terminal_turn_kind(events) == "error"
    assert codec.error_events(events) == []
    assert codec.final_text(events) == ""


def test_pre_turn_failure_has_error_and_no_final() -> None:
    events = codec.parse_event_stream(
        '{"type":"error","message":"session \\"x\\" does not exist"}'
    )

    assert codec.terminal_turn_reason(events) is None
    assert len(codec.error_events(events)) == 1
    assert not any(event.get("type") == "final" for event in events)


def test_sdk_projection_appends_one_faithful_terminal_marker() -> None:
    events = codec.parse_event_stream(REAL_PROBE_STREAM)

    projected = codec.to_sdk_events(events)

    assert len(projected) == len(events) + 1
    assert projected[-1] == {
        "type": "turn/end",
        "data": {"reason": {"kind": "completed"}},
    }
    # Every original event is retained verbatim.
    assert projected[: len(events)] == events


def test_sdk_projection_invents_nothing_when_no_terminal_event() -> None:
    events = codec.parse_event_stream('{"type":"text","text":"hi"}')

    assert codec.to_sdk_events(events) == events


@pytest.mark.parametrize(
    "secret",
    [
        "sk-abcdefghijklmnop",
        "Authorization: Bearer abcdefghijklmnop",
        "api_key=supersecretvalue",
    ],
)
def test_redaction_masks_credentials(secret: str) -> None:
    redacted = codec.redact_text(secret)

    assert "sk-abcdefghijklmnop" not in redacted or "sk-<redacted>" in redacted
    assert "abcdefghijklmnop" not in redacted or "supersecretvalue" not in redacted
    assert "supersecretvalue" not in redacted


def test_redaction_bounds_length() -> None:
    assert len(codec.redact_text("x" * 5_000, limit=100)) == 100


def test_retained_events_are_redacted_and_bounded() -> None:
    events = codec.parse_event_stream(
        '{"type":"tool_result","callId":"c","status":"completed",'
        '"result":"sk-abcdefghijklmnop"}'
    )

    retained = codec.retained_events(events)

    assert "sk-abcdefghijklmnop" not in json.dumps(retained)
    assert len(codec.retained_events(events * 700)) == codec.MAX_RETAINED_EVENTS


def test_retained_events_survive_large_payloads() -> None:
    """Regression: redaction must not truncate the *serialized* document.

    The governed-turn path rejected a real Turn because a large event was
    serialized, truncated by the string bound and then failed to parse, which
    the adapter recorded as a content-free ``unknown`` host failure.
    """

    events = codec.parse_event_stream(
        json.dumps(
            {
                "type": "tool_result",
                "callId": "c1",
                "status": "completed",
                "result": "x" * 20_000,
            }
        )
    )

    retained = codec.retained_events(events)

    assert len(retained) == 1
    # Still a serializable document, with the long field bounded per string.
    assert len(json.dumps(retained)) < 20_000
    assert retained[0]["callId"] == "c1"
    assert len(retained[0]["result"]) == codec.MAX_RETAINED_TEXT


def test_redaction_bounds_each_string_independently() -> None:
    redacted = codec.redact_structure(
        {"a": "y" * 5_000, "b": ["z" * 5_000], "n": 7, "t": True, "nil": None}
    )

    assert len(redacted["a"]) == codec.MAX_RETAINED_TEXT
    assert len(redacted["b"][0]) == codec.MAX_RETAINED_TEXT
    assert redacted["n"] == 7
    assert redacted["t"] is True
    assert redacted["nil"] is None


def test_redaction_masks_nested_credentials() -> None:
    redacted = codec.redact_structure(
        {"outer": {"api_key": "sk-abcdefghijklmnop"}, "list": ["Bearer abcdefghijklmnop"]}
    )

    assert "sk-abcdefghijklmnop" not in json.dumps(redacted)
    assert "abcdefghijklmnop" not in json.dumps(redacted)


def test_describe_stream_is_public_safe_summary() -> None:
    events = codec.parse_event_stream(REAL_PROBE_STREAM)

    described = codec.describe_stream(events)

    assert described["event_count"] == 13
    assert described["terminal_turn_kind"] == "completed"
    assert described["has_final"] is True
    assert described["error_count"] == 0
    assert described["event_type_counts"]["tool_call"] == 1

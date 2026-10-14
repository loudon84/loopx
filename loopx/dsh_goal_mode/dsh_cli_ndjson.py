"""NDJSON codec for the ``dsh --profile headless --json`` event stream.

This module is pure: it parses and classifies text and never starts a process,
touches LoopX state or spends quota. It is the only place that knows the shape
of a DeepSeek Harness 0.2 CLI run event.

The event vocabulary and the completion rule were measured against the
installed ``@deepseek-ai/dsh-headless@0.2.0-rc.2`` package (see
``docs/implementation/dsh-coding-worker-phase0.md``):

* every line is one JSON object carrying a ``type``;
* ``status`` events carry ``phase`` in
  ``turn_start | step_start | step_end | turn_end``;
* the run opens with ``session`` (``sessionId`` plus the recorded ``cwd``) and
  closes with ``final`` (``text``);
* ``text``, ``thinking``, ``tool_call`` and ``tool_result`` appear in between;
* a failure the runner raises outside a turn writes ``error`` and ends the
  stream **without** ``final``;
* an in-turn failure still ends with ``final`` (often empty) and **no**
  ``error``.

The last point is why completion is decided by exit status plus the terminal
``turn_end.reason.kind`` and never by the presence of ``final``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

#: Event types the installed 0.2.0-rc.2 launcher can emit.
CLI_EVENT_TYPES = frozenset(
    {
        "session",
        "status",
        "text",
        "thinking",
        "tool_call",
        "tool_result",
        "final",
        "error",
    }
)

#: ``status.phase`` values the launcher can emit.
STATUS_PHASES = frozenset({"turn_start", "step_start", "step_end", "turn_end"})

#: The launcher reports this ``turn_end.reason.kind`` for a completed turn.
COMPLETED_TURN_KIND = "completed"

#: Bound retained evidence so one hostile turn cannot balloon the journal.
MAX_RETAINED_EVENTS = 512
MAX_RETAINED_TEXT = 2_000

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Provider keys and bearer credentials.
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "sk-<redacted>"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"), "Bearer <redacted>"),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password)"
            r"\b\s*[=:]\s*[\"']?([^\s\"',}]{4,})"
        ),
        r"\1=<redacted>",
    ),
)


class DshCliProtocolError(ValueError):
    """The CLI wrote something that is not a valid run event stream."""


def redact_text(value: object, *, limit: int = MAX_RETAINED_TEXT) -> str:
    """Return bounded, credential-free text safe to persist or display."""

    text = str(value or "")
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    if len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return text


def parse_event_line(line: str) -> dict[str, Any] | None:
    """Parse one NDJSON line.

    Blank lines are ignored (``None``). A non-blank line that is not a JSON
    object raises: silently skipping it would hide a truncated or interleaved
    stream, which is exactly the failure this codec exists to catch.
    """

    stripped = line.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise DshCliProtocolError(f"invalid NDJSON event line: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise DshCliProtocolError("NDJSON event line is not a JSON object")
    event_type = parsed.get("type")
    if not isinstance(event_type, str) or not event_type:
        raise DshCliProtocolError("NDJSON event has no string type")
    return parsed


def parse_event_stream(text: str) -> list[dict[str, Any]]:
    """Parse a whole stdout capture into events, preserving order."""

    events: list[dict[str, Any]] = []
    for line in str(text or "").splitlines():
        event = parse_event_line(line)
        if event is not None:
            events.append(event)
    return events


def session_identity(events: Sequence[dict[str, Any]]) -> dict[str, str]:
    """Return the opening ``session`` identity the run reported."""

    for event in events:
        if event.get("type") != "session":
            continue
        session_id = event.get("sessionId")
        cwd = event.get("cwd")
        return {
            "session_id": session_id if isinstance(session_id, str) else "",
            "cwd": cwd if isinstance(cwd, str) else "",
        }
    return {"session_id": "", "cwd": ""}


def terminal_turn_reason(
    events: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return the last ``status(turn_end).reason`` object, if any."""

    for event in reversed(list(events)):
        if event.get("type") != "status":
            continue
        if event.get("phase") != "turn_end":
            continue
        reason = event.get("reason")
        if isinstance(reason, dict):
            return dict(reason)
        return None
    return None


def terminal_turn_kind(events: Sequence[dict[str, Any]]) -> str | None:
    """Return the terminal ``turn_end.reason.kind`` string, if any."""

    reason = terminal_turn_reason(events)
    if reason is None:
        return None
    kind = reason.get("kind")
    return kind if isinstance(kind, str) else None


def final_text(events: Sequence[dict[str, Any]]) -> str:
    """Return the last ``final`` event text (``""`` when no final arrived)."""

    for event in reversed(list(events)):
        if event.get("type") != "final":
            continue
        text = event.get("text")
        return text if isinstance(text, str) else ""
    return ""


def error_events(events: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the runner-level ``error`` events (pre-turn failures)."""

    return [dict(event) for event in events if event.get("type") == "error"]


def accumulate_usage(events: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Sum the ``step_end.usage`` counters the launcher reported.

    Only fields the launcher actually sent are returned; a caller must render a
    missing counter as unknown rather than as zero.
    """

    totals: dict[str, int] = {}
    for event in events:
        if event.get("type") != "status" or event.get("phase") != "step_end":
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict):
            continue
        for key, value in usage.items():
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            totals[key] = totals.get(key, 0) + value
    return totals


def tool_summaries(events: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pair ``tool_call``/``tool_result`` by ``callId`` into bounded summaries.

    Tool results can carry sensitive material, so every retained string passes
    through :func:`redact_text`.
    """

    results: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("type") != "tool_result":
            continue
        call_id = event.get("callId")
        if isinstance(call_id, str):
            results[call_id] = event

    summaries: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") != "tool_call":
            continue
        call_id = event.get("callId")
        matched = results.get(call_id) if isinstance(call_id, str) else None
        summary: dict[str, Any] = {
            "tool": redact_text(event.get("tool"), limit=120),
            "input": redact_text(
                json.dumps(event.get("input"), ensure_ascii=False, sort_keys=True),
                limit=600,
            ),
        }
        if matched is not None:
            summary["status"] = redact_text(matched.get("status"), limit=60)
            summary["result"] = redact_text(matched.get("result"), limit=2_000)
        else:
            summary["status"] = "unmatched"
        summaries.append(summary)
    return summaries


def to_sdk_events(events: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project CLI events onto the SDK-shaped ``turn/end`` marker LoopX reads.

    LoopX's governed-turn adapter inspects a runner outcome's events for the
    last ``{"type": "turn/end", "data": {"reason": {...}}}`` object and refuses
    an outcome whose ``finish_reason`` disagrees with it. The CLI reports the
    same fact as a ``status`` event with ``phase == "turn_end"``.

    This function **appends** one faithful projection of that real terminal
    event to the parsed CLI events. Nothing is invented: the reason object is
    copied verbatim from the measured stream, and every original event is
    retained so the raw evidence stays inspectable. The projection exists so
    LoopX's existing cross-check is active rather than inert for CLI runs.
    """

    projected = [dict(event) for event in events]
    reason = terminal_turn_reason(events)
    if reason is not None:
        projected.append({"type": "turn/end", "data": {"reason": reason}})
    return projected


def redact_structure(
    value: Any, *, limit: int = MAX_RETAINED_TEXT, depth: int = 0
) -> Any:
    """Recursively redact every string inside a JSON-shaped value.

    Redaction must never run over *serialized* JSON: a length bound applied to
    the whole document truncates it and the result no longer parses. Walking the
    structure keeps the document valid and bounds each string independently.
    """

    if depth > 64:
        return "[truncated: depth]"
    if isinstance(value, str):
        return redact_text(value, limit=limit)
    if isinstance(value, Mapping):
        return {
            str(key): redact_structure(item, limit=limit, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_structure(item, limit=limit, depth=depth + 1) for item in value]
    return value


def retained_events(
    events: Sequence[dict[str, Any]],
    *,
    limit: int = MAX_RETAINED_EVENTS,
) -> list[dict[str, Any]]:
    """Return a bounded, redacted copy of the event stream for the journal."""

    return [redact_structure(dict(event)) for event in list(events)[:limit]]


def format_diagnostics(stderr: str, *, limit: int = 4_000) -> str:
    """Return bounded, redacted stderr diagnostics for the failure record."""

    return redact_text(stderr, limit=limit)


def describe_stream(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Return a compact, public-safe summary of one parsed event stream."""

    materialized = list(events)
    counts: dict[str, int] = {}
    for event in materialized:
        key = str(event.get("type") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    identity = session_identity(materialized)
    return {
        "event_count": len(materialized),
        "event_type_counts": counts,
        "session_id": identity["session_id"],
        "cwd": identity["cwd"],
        "terminal_turn_kind": terminal_turn_kind(materialized),
        "has_final": any(event.get("type") == "final" for event in materialized),
        "error_count": len(error_events(materialized)),
        "usage": accumulate_usage(materialized),
    }

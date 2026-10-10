"""Read-only run readback for one Goal's governed Turns (FR-11 / AC-15).

What this is
------------
A projection of the recorded Turn journals into the facts a Web read needs to
make execution *traceable*: which Turn, which Todo, when it ran, how it ended,
whether an independent validator passed, which phases completed, and -- when it
failed -- the typed host failure kind.

What this is not
----------------
It is not evidence and it is not authority. The Turn journal remains the source;
this only re-shapes what is already recorded. It never re-runs a host, never
settles anything, never writes state, and never spends quota.

Privacy
-------
The projection is deliberately public-safe: Turn keys are reduced to a short
stable label, and no local path, Session id, DSH home, credential or raw tool
output is projected. Only the exact fields the AC names travel.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKER_RUN_READBACK_SCHEMA_VERSION = "loopx_worker_run_readback_v1"
DEFAULT_RUN_LIMIT = 20
_MAX_LIMIT = 200


def _deep_get(payload: Any, key: str, _depth: int = 0) -> Any:
    """Find a field without assuming the journal's nesting depth."""

    if _depth > 6:
        return None
    if isinstance(payload, Mapping):
        if key in payload and payload[key] is not None:
            return payload[key]
        for value in payload.values():
            found = _deep_get(value, key, _depth + 1)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _deep_get(value, key, _depth + 1)
            if found is not None:
                return found
    return None


def _short_turn_label(turn_key: str) -> str:
    """Reduce a Turn key to a stable, non-reversible label."""

    text = str(turn_key or "")
    digest = text.split(":", 1)[1] if ":" in text else text
    return digest[:12] if digest else ""


def _validation_projection(journal: Mapping[str, Any]) -> dict[str, Any]:
    """Project the independent validation verdict, if one is recorded."""

    validation = _deep_get(journal, "validation")
    if not isinstance(validation, Mapping):
        # The settlement receipt carries the same verdict on committed Turns.
        validation = _deep_get(journal, "task_validation")
    if not isinstance(validation, Mapping):
        return {"status": "unknown", "passed": None}
    status = str(validation.get("status") or "unknown")
    passed = status == "passed"
    return {
        "status": status,
        "passed": passed,
        "exit_code": validation.get("exit_code"),
        "validator_kind": validation.get("validator_kind"),
        "recovery_kind": validation.get("recovery_kind"),
    }


def project_turn_journal(
    journal: Mapping[str, Any], *, recorded_at: str | None = None
) -> dict[str, Any]:
    """Project one recorded Turn journal into a public-safe run row."""

    phases = _deep_get(journal, "completed_phases")
    host_failure = _deep_get(journal, "host_failure")
    effects = _deep_get(journal, "recorded_effects") or {}
    return {
        "schema_version": WORKER_RUN_READBACK_SCHEMA_VERSION,
        "turn_label": _short_turn_label(str(journal.get("turn_key") or "")),
        "turn_instance_id": str(_deep_get(journal, "turn_instance_id") or ""),
        "todo_id": str(_deep_get(journal, "todo_id") or ""),
        # The journal records no wall-clock field, so the only honest timestamp
        # is when the journal file itself was written. Labelled, never dressed up
        # as a Turn start/end time.
        "recorded_at": recorded_at,
        "recorded_at_source": "journal_file_mtime" if recorded_at else None,
        "status": str(journal.get("status") or "unknown"),
        "result_kind": str(journal.get("result_kind") or "unknown"),
        "completed_phases": list(phases) if isinstance(phases, list) else [],
        "validation": _validation_projection(journal),
        "host_kind": str(_deep_get(journal, "kind") or ""),
        "host_failure_kind": (
            str(host_failure.get("kind")) if isinstance(host_failure, Mapping) else None
        ),
        "host_attempt_count": journal.get("host_attempt_count"),
        "recovery_from": _deep_get(journal, "recovery_from"),
        "host_invoked": bool(effects.get("host_invoked"))
        if isinstance(effects, Mapping)
        else None,
        "quota_spent": bool(effects.get("quota_spent"))
        if isinstance(effects, Mapping)
        else None,
        "reason": str(journal.get("reason") or "") or None,
    }


def goal_turn_history(
    *,
    goal_id: str,
    runtime_root: Path,
    limit: int = DEFAULT_RUN_LIMIT,
) -> dict[str, Any]:
    """Return the recorded Turns of one Goal, newest first.

    A missing or unreadable journal directory is an empty history, not an error:
    this is a display read and a Goal that never ran legitimately has none.
    """

    bounded = max(1, min(int(limit), _MAX_LIMIT))
    turns_dir = Path(runtime_root).expanduser() / "goals" / str(goal_id) / "turns"
    rows: list[dict[str, Any]] = []
    if turns_dir.is_dir():
        for path in sorted(turns_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                written = path.stat().st_mtime
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, Mapping):
                continue
            rows.append(
                project_turn_journal(
                    payload,
                    recorded_at=datetime.fromtimestamp(written, tz=timezone.utc)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z"),
                )
            )

    # Newest first. Journal names are content hashes, so order by the recorded
    # write time rather than by name.
    rows.sort(key=lambda row: str(row.get("recorded_at") or ""), reverse=True)
    return {
        "schema_version": WORKER_RUN_READBACK_SCHEMA_VERSION,
        "goal_id": str(goal_id),
        "turn_count": len(rows),
        "returned": min(len(rows), bounded),
        "runs": rows[:bounded],
    }

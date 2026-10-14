"""Unit tests for the governed Turn host loop.

These guard the two parsing defects that a real end-to-end run exposed, both of
which silently turned a settleable Turn into an "unknown" stop:

* ``turn run-once --format json`` nests the Turn facts differently from the flat
  markdown summary;
* LoopX reports a *single* eligible Todo on the agent channel as
  ``"<todo_id>: <text>"`` and only fills the multi-action portfolio when it has
  alternatives.

No CLI, model or network is involved here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from loopx.control_plane import worker_catalog
from loopx.dsh_goal_mode import governed_turn_loop as loop


def test_single_eligible_todo_is_read_from_the_agent_channel() -> None:
    payload = {
        "should_run": True,
        "interaction_contract": {
            "agent_channel": {"primary_action": "todo_abc123: add src/hello.py"}
        },
        "action_portfolio": {"primary": None, "suggested_actions": None},
    }

    assert loop._eligible_todo_ids(payload) == ["todo_abc123"]


def test_multiple_candidates_keep_the_recommended_order() -> None:
    payload = {
        "action_portfolio": {
            "primary": {"todo_id": "todo_first"},
            "suggested_actions": [
                {"todo_id": "todo_first"},
                {"todo_id": "todo_second"},
            ],
        }
    }

    assert loop._eligible_todo_ids(payload) == ["todo_first", "todo_second"]


def test_no_candidates_reports_empty_rather_than_guessing() -> None:
    assert loop._eligible_todo_ids({"action_portfolio": {}}) == []


def test_turn_report_is_read_through_nesting() -> None:
    """The same facts arrive nested under `--format json`."""

    payload = {
        "ok": True,
        "report": {
            "turn": {
                "status": "committed",
                "result_kind": "validated_progress",
                "validation": {"status": "passed"},
                "host_invoked": False,
                "completed_phases": ["host_execute", "validation", "durable_writeback"],
            },
            "recovery_from": "durable_writeback",
        },
    }

    record = loop._parse_turn_report(payload)

    assert record.status == "committed"
    assert record.result_kind == "validated_progress"
    assert record.host_invoked is False
    assert record.phases == ["host_execute", "validation", "durable_writeback"]
    assert record.recovery_from == "durable_writeback"


def test_fence_detection_finds_the_marker_at_any_depth() -> None:
    payload = {"a": {"b": [{"recovery_from": "durable_writeback"}]}}

    assert loop._deep_contains(payload, "durable_writeback") is True
    assert loop._deep_contains(payload, "something_else") is False


def test_missing_final_status_is_not_reported_as_committed() -> None:
    record = loop._parse_turn_report({"error": "accountable refresh is blocked"})

    assert record.status == "unknown"
    assert record.stop_reason == "accountable refresh is blocked"


def test_runtime_root_falls_back_when_the_registry_is_unreadable(tmp_path) -> None:
    resolved = loop._runtime_root_for(tmp_path / "missing" / "registry.json", tmp_path)

    assert resolved == __import__("pathlib").Path.home() / ".loopx"


def test_runtime_root_prefers_the_declared_common_root(tmp_path) -> None:
    import json

    registry = tmp_path / "registry.json"
    runtime = tmp_path / "runtime"
    registry.write_text(
        json.dumps({"common_runtime_root": str(runtime)}), encoding="utf-8"
    )

    assert loop._runtime_root_for(registry, tmp_path) == runtime


def test_in_progress_key_is_none_without_a_turns_directory(tmp_path) -> None:
    assert (
        loop._in_progress_turn_key(
            goal_id="g", registry_path=None, project=tmp_path
        )
        is None
    )


def test_loop_error_is_raised_when_loopx_is_unavailable(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(loop.LoopError):
        loop._run_cli(["loopx", "--version"], cwd=tmp_path, timeout=5)


# --------------------------------------------------------------------------
# Continuation declaration (LoopX's autonomous_replan_required obligation)
# --------------------------------------------------------------------------


def test_single_blocked_todo_becomes_the_declared_successor(monkeypatch) -> None:
    monkeypatch.setattr(
        loop, "_blocked_todo_ids", lambda **_kwargs: ["todo_dependent"]
    )

    declaration = loop._successor_declaration(
        base=["loopx"], goal_id="g", project=__import__("pathlib").Path("."),
        timeout_seconds=5,
    )

    assert declaration == ["--successor-todo-id", "todo_dependent"]


def test_no_blocked_todo_declares_no_follow_up(monkeypatch) -> None:
    monkeypatch.setattr(loop, "_blocked_todo_ids", lambda **_kwargs: [])

    declaration = loop._successor_declaration(
        base=["loopx"], goal_id="g", project=__import__("pathlib").Path("."),
        timeout_seconds=5,
    )

    assert declaration == ["--no-follow-up"]


def test_ambiguous_successors_are_not_guessed(monkeypatch) -> None:
    """Two blocked candidates must keep LoopX's obligation, not pick one."""

    monkeypatch.setattr(
        loop, "_blocked_todo_ids", lambda **_kwargs: ["todo_a", "todo_b"]
    )

    declaration = loop._successor_declaration(
        base=["loopx"], goal_id="g", project=__import__("pathlib").Path("."),
        timeout_seconds=5,
    )

    assert declaration == []


# --------------------------------------------------------------------------
# Stop classification and AC-10 accounting
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("paused", "paused"),
        ("user_gate", "gate"),
        ("blocked", "gate"),
        ("quota_exhausted", "quota"),
        ("exhausted", "quota"),
        ("budget_exhausted", "quota"),
        ("something_else", "not_admitted"),
    ],
)
def test_stop_kinds_group_loopx_states(state: str, expected: str) -> None:
    assert loop.classify_stop_kind(state, "") == expected


def test_stop_kind_falls_back_to_the_reason_text() -> None:
    assert loop.classify_stop_kind("", "goal is stopped by owner") == "paused"
    assert loop.classify_stop_kind("", "waiting on a user_gate") == "gate"
    assert loop.classify_stop_kind("", "quota exhausted for today") == "quota"


def test_outcome_reports_host_invocations_and_stop_kind() -> None:
    """AC-10 is asserted from these two fields, not from prose."""

    payload = loop.LoopOutcome(
        turns=[], stop_reason="paused", stop_detail="paused", stop_kind="paused",
        host_invocations=0,
    ).as_dict()

    assert payload["host_invocations"] == 0
    assert payload["stop_kind"] == "paused"


def test_cancel_stops_before_any_host_invocation(monkeypatch) -> None:
    """A pre-set cancel must not ask LoopX for work, let alone launch a host."""

    import threading

    called = {"n": 0}

    def _boom(*_args, **_kwargs):
        called["n"] += 1
        raise AssertionError("the loop must not call the CLI when cancelled")

    monkeypatch.setattr(loop, "_run_json", _boom)
    cancel = threading.Event()
    cancel.set()

    outcome = loop.run_host_loop(
        goal_id="g", agent_id="a", project=__import__("pathlib").Path("."),
        dsh_runner=__import__("pathlib").Path("runner.py"),
        cancel=cancel,
    )

    assert called["n"] == 0
    assert outcome.stop_kind == "cancelled"
    assert outcome.host_invocations == 0


# --------------------------------------------------------------------------
# Precise prepared-Turn identity (never settle the wrong journal)
# --------------------------------------------------------------------------


def _journal(tmp_path, *, goal: str, name: str, status: str, turn_instance_id: str, todo_id: str):
    root = tmp_path / "runtime"
    turns = root / "goals" / goal / "turns"
    turns.mkdir(parents=True, exist_ok=True)
    registry = tmp_path / "registry.json"
    registry.write_text('{"common_runtime_root": %s}' % __import__("json").dumps(str(root)))
    (turns / f"{name}.json").write_text(
        __import__("json").dumps(
            {
                "turn_key": f"sha256:{name}",
                "status": status,
                "plan": {"transaction": {"turn_instance_id": turn_instance_id}},
                "route": {"selected_todo": {"todo_id": todo_id}},
            }
        )
    )
    return registry


def test_prepared_turn_is_matched_by_exact_identity(tmp_path) -> None:
    registry = _journal(
        tmp_path, goal="g", name="stale", status="in_progress",
        turn_instance_id="turn-old", todo_id="todo_a",
    )
    _journal(
        tmp_path, goal="g", name="current", status="in_progress",
        turn_instance_id="turn-new", todo_id="todo_b",
    )

    resolved = loop._in_progress_turn_key(
        goal_id="g", registry_path=registry, project=tmp_path,
        turn_instance_id="turn-new", todo_id="todo_b",
    )

    assert resolved == "sha256:current"


def test_unknown_identity_settles_nothing(tmp_path) -> None:
    """A journal left by another attempt must never be settled for this Turn."""

    registry = _journal(
        tmp_path, goal="g", name="stale", status="in_progress",
        turn_instance_id="turn-old", todo_id="todo_a",
    )

    assert (
        loop._in_progress_turn_key(
            goal_id="g", registry_path=registry, project=tmp_path,
            turn_instance_id="turn-never-seen",
        )
        is None
    )


def test_ambiguous_candidates_without_identity_settle_nothing(tmp_path) -> None:
    registry = _journal(
        tmp_path, goal="g", name="one", status="in_progress",
        turn_instance_id="turn-1", todo_id="todo_a",
    )
    _journal(
        tmp_path, goal="g", name="two", status="in_progress",
        turn_instance_id="turn-2", todo_id="todo_b",
    )

    assert (
        loop._in_progress_turn_key(goal_id="g", registry_path=registry, project=tmp_path)
        is None
    )


def test_terminal_journals_are_never_settled(tmp_path) -> None:
    registry = _journal(
        tmp_path, goal="g", name="done", status="committed",
        turn_instance_id="turn-1", todo_id="todo_a",
    )

    assert (
        loop._in_progress_turn_key(
            goal_id="g", registry_path=registry, project=tmp_path, turn_instance_id="turn-1"
        )
        is None
    )


@pytest.mark.parametrize(
    ("payload", "expected_kind"),
    [
        (
            {"should_run": False, "decision": "skip", "state": "paused",
             "reason": "goal is stopped by owner; automatic agent turns are paused"},
            "paused",
        ),
        (
            {"should_run": False, "decision": "skip", "state": "user_gate",
             "reason": "an open user gate blocks delivery"},
            "gate",
        ),
        (
            {"should_run": False, "decision": "skip", "state": "quota_exhausted",
             "reason": "the compute quota for this window is spent"},
            "quota",
        ),
        (
            {"should_run": False, "decision": "skip", "state": "exhausted",
             "reason": "the compute quota for this window is spent"},
            "quota",
        ),
    ],
)
def test_every_refusal_short_circuits_before_the_host(
    monkeypatch, payload: dict, expected_kind: str
) -> None:
    """AC-10: a refusal must never reach the host, whatever its reason.

    ``paused`` and ``gate`` are additionally proven end to end against a real
    LoopX; this covers the whole refusal class, including a spent quota, without
    fabricating a quota state on the live system.
    """

    calls = {"n": 0}

    def _only_should_run(argv, **_kwargs):
        calls["n"] += 1
        assert "should-run" in argv, "a refusal must stop before any Turn is built"
        return payload

    monkeypatch.setattr(loop, "_run_json", _only_should_run)

    outcome = loop.run_host_loop(
        goal_id="g", agent_id="a", project=__import__("pathlib").Path("."),
        dsh_runner=__import__("pathlib").Path("runner.py"),
        max_turns=3,
    )

    assert outcome.host_invocations == 0
    assert outcome.turns == []
    assert outcome.stop_kind == expected_kind
    # Exactly one guard call: the loop asked once and obeyed the answer.
    assert calls["n"] == 1


def test_settlement_fence_recognises_more_than_one_report_shape() -> None:
    """Regression: a live run reported the fence without the durable_writeback
    literal, so the loop treated a settleable Turn as a failure."""

    record = loop.TurnRecord(
        turn_instance_id="t", todo_id="todo_a", action="continue", status="observed",
        result_kind="unknown", validation="unknown", host_invoked=True,
    )

    # The shape a real run produced.
    assert loop._is_settlement_fence(
        record,
        {"error": "accountable refresh is blocked until controller-declared "
                  "completion validation durably completes todo todo_a"},
    )
    # The originally-recognised shape must keep working.
    assert loop._is_settlement_fence(
        record, {"recorded_effects": {"host_invoked": True}, "recovery_from": "durable_writeback"}
    )
    assert loop._is_settlement_fence(record, {"recovery_from": "durable_writeback"})
    # A committed Turn is never a fence, and an unrelated failure is not one.
    committed = loop.TurnRecord(
        turn_instance_id="t", todo_id="todo_a", action="continue", status="committed",
        result_kind="validated_progress", validation="passed", host_invoked=True,
    )
    assert not loop._is_settlement_fence(committed, {"recovery_from": "durable_writeback"})
    assert not loop._is_settlement_fence(record, {"error": "host could not start"})


def test_dsh_turn_argv_keeps_historical_shape(tmp_path: Path) -> None:
    descriptor = worker_catalog.resolve_worker(
        "dsh-coding-worker-local",
        environment={worker_catalog.WORKER_FEATURE_FLAG_ENV: "1"},
        require_enabled=False,
    )
    assert descriptor is not None
    argv = loop.build_turn_run_once_argv(
        base=["loopx", "--format", "json"],
        goal_id="g1",
        agent_id="a1",
        project=tmp_path,
        todo_id="todo_a",
        turn_instance_id="turn-1",
        timeout_seconds=1800,
        descriptor=descriptor,
        dsh_runner=tmp_path / "runner.py",
        dsh_home=tmp_path / "dsh-home",
    )
    assert argv[argv.index("--host") + 1] == "dsh"
    assert argv[argv.index("--dsh-runner") + 1] == str(tmp_path / "runner.py")
    assert argv[argv.index("--dsh-home") + 1] == str(tmp_path / "dsh-home")
    assert argv[-1] == "--execute"


def test_codex_and_cursor_turn_argv_dispatch(tmp_path: Path) -> None:
    codex = worker_catalog.resolve_worker(
        "codex-cli-coding-worker-local",
        environment={worker_catalog.CODEX_WORKER_FEATURE_FLAG_ENV: "1"},
        require_enabled=False,
    )
    cursor = worker_catalog.resolve_worker(
        "cursor-cli-coding-worker-local",
        environment={worker_catalog.CURSOR_WORKER_FEATURE_FLAG_ENV: "1"},
        require_enabled=False,
    )
    assert codex is not None and cursor is not None

    codex_argv = loop.build_turn_run_once_argv(
        base=["loopx"],
        goal_id="g1",
        agent_id="a1",
        project=tmp_path,
        todo_id="todo_a",
        turn_instance_id="turn-1",
        timeout_seconds=60,
        descriptor=codex,
        dsh_runner=None,
        codex_bin="/opt/codex",
    )
    assert codex_argv[codex_argv.index("--host") + 1] == "codex-cli"
    assert "--dsh-runner" not in codex_argv
    assert codex_argv[codex_argv.index("--codex-bin") + 1] == "/opt/codex"

    cursor_argv = loop.build_turn_run_once_argv(
        base=["loopx"],
        goal_id="g1",
        agent_id="a1",
        project=tmp_path,
        todo_id="todo_a",
        turn_instance_id="turn-1",
        timeout_seconds=60,
        descriptor=cursor,
        dsh_runner=None,
        cursor_adapter_command=["python", "-m", "loopx.cursor_worker.turn_host_adapter"],
    )
    assert cursor_argv[cursor_argv.index("--host") + 1] == "generic-cli"
    assert "--host-adapter-command-json" in cursor_argv


def test_goal_registry_selects_worker_endpoint(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(
        '{"goals":[{"id":"g1","worker_endpoint_id":"codex-cli-coding-worker-local"}]}'
    )
    assert (
        loop._goal_worker_endpoint_id(
            goal_id="g1", registry_path=registry, explicit=None
        )
        == "codex-cli-coding-worker-local"
    )
    assert (
        loop._goal_worker_endpoint_id(
            goal_id="g1",
            registry_path=registry,
            explicit="cursor-cli-coding-worker-local",
        )
        == "cursor-cli-coding-worker-local"
    )

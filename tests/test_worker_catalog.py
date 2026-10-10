"""The governed-turn Coding Worker route for Goal creation.

Guards the exact defect the PRD exists to fix: creating a Goal for an
execution-capable worker must not be adjudicated by the read-only Chat
endpoint's tool check, and the Chat route must keep behaving exactly as before.

The worker's availability verdict is always produced by a live probe against a
hermetic fake launcher, so these tests never need a real model, network or
DeepSeek Harness install.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.bootstrap import bootstrap_project
from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.control_plane import worker_catalog
from tests.dsh_cli_fake_launcher import write_fake_launcher

WORKER_ENDPOINT_ID = "dsh-coding-worker-local"


@pytest.fixture(autouse=True)
def _worker_capability_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The capability ships dark; every test starts from the shipped default."""

    monkeypatch.delenv(worker_catalog.WORKER_FEATURE_FLAG_ENV, raising=False)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "p2-project"
    (root / "src").mkdir(parents=True)
    (root / "README.md").write_text("p2 testbed\n")
    return root


@pytest.fixture()
def dsh_home(tmp_path: Path) -> Path:
    home = tmp_path / "dsh-home"
    home.mkdir()
    (home / ".credentials.yaml").write_text("deepseek: test\n")
    return home


@pytest.fixture()
def worker_env(
    tmp_path: Path, dsh_home: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    launcher = write_fake_launcher(tmp_path)
    monkeypatch.setenv(worker_catalog.WORKER_FEATURE_FLAG_ENV, "1")
    monkeypatch.setenv("LOOPX_DSH_CODING_WORKER_BIN", str(launcher))
    monkeypatch.setenv("DSH_HOME", str(dsh_home))
    monkeypatch.setenv("FAKE_DSH_MODE", "ok")
    return launcher


def _registry(project: Path, tmp_path: Path) -> Path:
    """Register the project with one source goal, as the Web has before creating."""

    registry = project / ".loopx" / "registry.json"
    bootstrap_project(
        project=project,
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id="p2-source-goal",
        objective="Provide the workspace for the Coding Worker Goal creation test.",
        domain="project-goal-control-plane",
        role="controller",
        parent_goal_id=None,
        state_file=None,
        goal_doc=None,
        adapter_kind="generic_project_goal_v0",
        adapter_status="connected",
        next_probe=None,
        spawn_allowed=False,
        max_children=3,
        allowed_domains=None,
        write_scope=[str(project)],
        force=False,
        dry_run=False,
        sync_global=False,
    )
    return registry


def _service(project: Path, tmp_path: Path) -> tuple[ChatActionService, Path]:
    registry = _registry(project, tmp_path)
    return (
        ChatActionService(
            store=ChatActionStore(tmp_path / "actions"), registry_path=registry
        ),
        registry,
    )


def _goals(registry: Path) -> dict[str, dict]:
    return {
        str(goal.get("id")): goal
        for goal in json.loads(registry.read_text()).get("goals", [])
    }


def _preview_and_apply(
    service: ChatActionService, parameters: dict, key: str
) -> dict:
    proposal = service.preview(
        {
            "action_kind": "goal.create",
            "summary": "Create the Goal",
            "context": {},
            "normalized_parameters": parameters,
            "idempotency_key": key,
        }
    )
    assert proposal["status"] == "preview_ready", proposal
    return service.apply(proposal["proposal_id"])["proposal"]


# --------------------------------------------------------------------------
# Catalog + admission
# --------------------------------------------------------------------------


def test_catalog_lists_the_builtin_worker() -> None:
    catalog = worker_catalog.worker_catalog_projection(
        workspace="/tmp",
        environment={worker_catalog.WORKER_FEATURE_FLAG_ENV: "1"},
        authorized_workspaces=["/tmp"],
    )

    assert catalog["schema_version"] == worker_catalog.WORKER_CATALOG_SCHEMA_VERSION
    assert [worker["worker_endpoint_id"] for worker in catalog["workers"]] == [
        WORKER_ENDPOINT_ID
    ]


def test_unregistered_worker_resolves_to_none() -> None:
    assert worker_catalog.resolve_worker("nope", environment={}) is None


def test_worker_capability_is_disabled_by_default() -> None:
    """Ships dark: no env, no worker, and a reason that says why."""

    assert worker_catalog.coding_worker_enabled({}) is False
    assert worker_catalog.resolve_worker(WORKER_ENDPOINT_ID, environment={}) is None
    assert (
        worker_catalog.worker_unavailable_reason(WORKER_ENDPOINT_ID, environment={})
        == "feature_flag_disabled"
    )
    catalog = worker_catalog.worker_catalog_projection(
        workspace="/tmp", environment={}, authorized_workspaces=["/tmp"]
    )
    assert catalog["workers"] == []
    assert catalog["feature_flag"]["enabled"] is False


@pytest.mark.parametrize("value", ["0", "false", "off", "", "no", "enabled"])
def test_only_a_truthy_flag_enables_the_capability(value: str) -> None:
    """An unrecognised value must not enable it; rollback clears the flag."""

    assert worker_catalog.coding_worker_enabled({worker_catalog.WORKER_FEATURE_FLAG_ENV: value}) is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_truthy_flag_enables_the_capability(value: str) -> None:
    assert worker_catalog.coding_worker_enabled({worker_catalog.WORKER_FEATURE_FLAG_ENV: value}) is True


def test_unknown_executor_route_is_rejected() -> None:
    with pytest.raises(ValueError):
        worker_catalog.normalize_executor_route("something_else")


def test_executor_route_defaults_to_the_chat_agent() -> None:
    assert (
        worker_catalog.normalize_executor_route(None)
        == worker_catalog.EXECUTOR_ROUTE_CHAT_AGENT
    )


def test_workspace_outside_the_authorized_roots_is_refused(tmp_path: Path) -> None:
    descriptor = worker_catalog.resolve_worker(
        WORKER_ENDPOINT_ID,
        environment={worker_catalog.WORKER_FEATURE_FLAG_ENV: "1"},
        authorized_workspaces=[tmp_path / "allowed"],
    )

    with pytest.raises(worker_catalog.WorkerNotAdmissible) as excinfo:
        worker_catalog.admit_worker(
            descriptor, workspace=tmp_path / "elsewhere", endpoint_id=WORKER_ENDPOINT_ID
        )

    assert "not authorized for this workspace" in str(excinfo.value)
    assert excinfo.value.gate["kind"] == "worker_workspace_not_authorized"


def test_missing_endpoint_is_refused(tmp_path: Path) -> None:
    with pytest.raises(worker_catalog.WorkerNotAdmissible) as excinfo:
        worker_catalog.admit_worker(
            None, workspace=tmp_path, endpoint_id=WORKER_ENDPOINT_ID
        )

    assert excinfo.value.gate["kind"] == "worker_endpoint_unknown"


def test_unavailable_binary_makes_the_worker_not_ready(
    tmp_path: Path, dsh_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DSH_HOME", str(dsh_home))
    descriptor = worker_catalog.resolve_worker(
        WORKER_ENDPOINT_ID,
        environment={
            worker_catalog.WORKER_FEATURE_FLAG_ENV: "1",
            "LOOPX_DSH_CODING_WORKER_BIN": str(tmp_path / "absent-dsh"),
        },
        authorized_workspaces=[tmp_path],
    )

    with pytest.raises(worker_catalog.WorkerNotAdmissible) as excinfo:
        worker_catalog.admit_worker(
            descriptor, workspace=tmp_path, endpoint_id=WORKER_ENDPOINT_ID
        )

    assert excinfo.value.gate["kind"] == "worker_not_ready"


def test_unwritable_dsh_home_makes_the_worker_not_ready(
    tmp_path: Path, worker_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "read-only-home"
    home.mkdir()
    home.chmod(0o500)
    monkeypatch.setenv("DSH_HOME", str(home))
    try:
        descriptor = worker_catalog.resolve_worker(
            WORKER_ENDPOINT_ID,
            authorized_workspaces=[tmp_path],
        )
        with pytest.raises(worker_catalog.WorkerNotAdmissible) as excinfo:
            worker_catalog.admit_worker(
                descriptor, workspace=tmp_path, endpoint_id=WORKER_ENDPOINT_ID
            )
        assert "dsh_home_writable" in str(excinfo.value)
    finally:
        home.chmod(0o700)


def test_public_snapshot_does_not_leak_local_paths(
    tmp_path: Path, dsh_home: Path, worker_env: Path
) -> None:
    descriptor = worker_catalog.resolve_worker(
        WORKER_ENDPOINT_ID, authorized_workspaces=[tmp_path]
    )

    snapshot = worker_catalog.public_worker_snapshot(descriptor, workspace=tmp_path)

    serialized = json.dumps(snapshot)
    assert str(dsh_home) not in serialized
    assert str(tmp_path) not in serialized
    assert snapshot["trust_scope"] == worker_catalog.TRUST_SCOPE_WORKSPACE_WRITE
    assert snapshot["tool_calls"] is True
    assert snapshot["availability"]["ready"] is True


# --------------------------------------------------------------------------
# goal.create dual route (the B-06 defect)
# --------------------------------------------------------------------------


def test_chat_agent_route_is_unchanged_when_no_route_is_given(
    project: Path, tmp_path: Path
) -> None:
    service, registry = _service(project, tmp_path)

    applied = _preview_and_apply(
        service,
        {"goal_id": "p2-default-goal", "title": "Default route", "agent_id": "codex"},
        "p2-default",
    )

    assert applied["status"] == "applied"
    goals = _goals(registry)
    assert goals["p2-default-goal"]["executor_route"] == "chat_agent"


def test_worker_route_creates_the_goal_without_the_chat_tool_error(
    project: Path, tmp_path: Path, worker_env: Path
) -> None:
    """The core PRD defect: this must not raise 'cannot execute Goal tools'."""

    service, registry = _service(project, tmp_path)

    applied = _preview_and_apply(
        service,
        {
            "goal_id": "p2-worker-goal",
            "title": "Coding Worker Goal",
            "objective": "Add src/hello.py and its test.",
            "executor_route": worker_catalog.EXECUTOR_ROUTE_GOVERNED_TURN_WORKER,
            "worker_endpoint_id": WORKER_ENDPOINT_ID,
            "initial_todos": [
                "Add src/hello.py returning Hello, {name}",
                "Add tests/test_hello.py asserting hello('LoopX')",
            ],
        },
        "p2-worker",
    )

    assert applied["status"] == "applied"
    goal = _goals(registry)["p2-worker-goal"]
    assert goal["executor_route"] == worker_catalog.EXECUTOR_ROUTE_GOVERNED_TURN_WORKER
    assert goal["worker_endpoint_id"] == WORKER_ENDPOINT_ID
    # No Chat agent identity was bound, which is what keeps the Chat session
    # opening branch out of the Coding Worker path entirely.
    assert not goal.get("agent_id") or goal.get("agent_id") != "codex"


def test_unknown_worker_endpoint_is_rejected_at_preview(
    project: Path, tmp_path: Path, worker_env: Path
) -> None:
    service, _ = _service(project, tmp_path)

    with pytest.raises(ValueError, match="not registered"):
        service.preview(
            {
                "action_kind": "goal.create",
                "summary": "Create",
                "context": {},
                "normalized_parameters": {
                    "goal_id": "p2-bad-worker",
                    "title": "Bad worker",
                    "executor_route": worker_catalog.EXECUTOR_ROUTE_GOVERNED_TURN_WORKER,
                    "worker_endpoint_id": "not-a-worker",
                },
                "idempotency_key": "p2-bad-worker",
            }
        )


def test_unknown_executor_route_is_rejected_at_preview(
    project: Path, tmp_path: Path
) -> None:
    service, _ = _service(project, tmp_path)

    with pytest.raises(ValueError, match="unsupported executor_route"):
        service.preview(
            {
                "action_kind": "goal.create",
                "summary": "Create",
                "context": {},
                "normalized_parameters": {
                    "goal_id": "p2-bad-route",
                    "title": "Bad route",
                    "executor_route": "not_a_route",
                },
                "idempotency_key": "p2-bad-route",
            }
        )


def test_worker_route_cannot_also_select_a_chat_agent_endpoint(
    project: Path, tmp_path: Path, worker_env: Path
) -> None:
    service, _ = _service(project, tmp_path)

    with pytest.raises(ValueError, match="must not also select a Chat agent"):
        service.preview(
            {
                "action_kind": "goal.create",
                "summary": "Create",
                "context": {},
                "normalized_parameters": {
                    "goal_id": "p2-mixed",
                    "title": "Mixed",
                    "agent_id": "codex",
                    "executor_route": worker_catalog.EXECUTOR_ROUTE_GOVERNED_TURN_WORKER,
                    "worker_endpoint_id": WORKER_ENDPOINT_ID,
                },
                "idempotency_key": "p2-mixed",
            }
        )


def test_worker_route_requires_an_available_worker(
    project: Path, tmp_path: Path, dsh_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A configured-but-unhealthy worker is refused, not silently admitted."""

    monkeypatch.setenv(worker_catalog.WORKER_FEATURE_FLAG_ENV, "1")
    monkeypatch.setenv("DSH_HOME", str(dsh_home))
    monkeypatch.setenv(
        "LOOPX_DSH_CODING_WORKER_BIN", str(tmp_path / "no-such-dsh")
    )
    service, registry = _service(project, tmp_path)

    with pytest.raises(worker_catalog.WorkerNotAdmissible):
        _preview_and_apply(
            service,
            {
                "goal_id": "p2-unavailable",
                "title": "Unavailable worker",
                "executor_route": worker_catalog.EXECUTOR_ROUTE_GOVERNED_TURN_WORKER,
                "worker_endpoint_id": WORKER_ENDPOINT_ID,
            },
            "p2-unavailable",
        )

    # Fail closed: no Goal is created when the worker cannot execute.
    assert "p2-unavailable" not in _goals(registry)


def test_worker_route_is_refused_while_the_capability_is_dark(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default-off must refuse the route with a reason naming the opt-in.

    This is the rollback guarantee: with the flag cleared, no Goal can be
    created against the Coding Worker, and the operator sees why.
    """

    monkeypatch.delenv(worker_catalog.WORKER_FEATURE_FLAG_ENV, raising=False)
    service, registry = _service(project, tmp_path)

    with pytest.raises(ValueError, match="disabled on this machine"):
        _preview_and_apply(
            service,
            {
                "goal_id": "p4-dark-goal",
                "title": "Dark worker",
                "executor_route": worker_catalog.EXECUTOR_ROUTE_GOVERNED_TURN_WORKER,
                "worker_endpoint_id": WORKER_ENDPOINT_ID,
            },
            "p4-dark",
        )

    assert "p4-dark-goal" not in _goals(registry)


def test_chat_agent_route_still_works_while_the_capability_is_dark(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rolling the worker back must not disturb the existing Chat route."""

    monkeypatch.delenv(worker_catalog.WORKER_FEATURE_FLAG_ENV, raising=False)
    service, registry = _service(project, tmp_path)

    applied = _preview_and_apply(
        service,
        {"goal_id": "p4-chat-goal", "title": "Chat still works", "agent_id": "codex"},
        "p4-chat",
    )

    assert applied["status"] == "applied"
    assert _goals(registry)["p4-chat-goal"]["executor_route"] == "chat_agent"

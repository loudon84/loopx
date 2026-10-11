"""Inspect, prepare, confirm and audit a canonical Goal Closeout.

Every action routes to the canonical authority. The CLI never writes a
completion state itself, and `confirm` records the owner principal supplied by
the trusted host — a CLI flag is not proof of identity, so the browser owner
session or a trusted OS/service policy remains the authentication authority.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..control_plane.goals.closeout import (
    confirm_goal_closeout,
    inspect_goal_closeout,
    prepare_goal_closeout,
    public_goal_closeout,
    read_goal_closeout_history,
)


def register_goal_closeout_command(subparsers: Any, add_format: Any) -> None:
    parser = subparsers.add_parser(
        "goal-closeout",
        help="Inspect, prepare, confirm or audit the canonical Goal Closeout transaction.",
    )
    add_format(parser)
    parser.add_argument("action", choices=("inspect", "prepare", "confirm", "history"))
    parser.add_argument("--goal-id", required=True)
    parser.add_argument(
        "--agent-id",
        help="Registered caller for inspect/history; an agent actor can never confirm.",
    )
    parser.add_argument(
        "--owner-principal",
        help="Trusted host owner principal. Required by prepare --execute and by confirm.",
    )
    parser.add_argument(
        "--expected-basis-digest",
        help="Exact basis digest returned by prepare; confirm refuses any other value.",
    )
    parser.add_argument(
        "--operation-id",
        help="Stable identity for an unchanged retry (idempotent replay).",
    )
    parser.add_argument("--execute", action="store_true", help="Apply; otherwise preview only.")


def render_goal_closeout(payload: dict[str, Any]) -> str:
    lines = ["# Goal closeout", "", f"- ok: {payload.get('ok')}"]
    if payload.get("error"):
        return "\n".join([*lines, f"- error: {payload['error']}"])
    if payload.get("provider_revision"):
        lines.append(f"- provider revision: {payload['provider_revision']}")
    closeout = payload.get("goal_closeout") or {}
    lines.append(f"- state: {payload.get('state') or closeout.get('state')}")
    lines.append(f"- completed: {closeout.get('completed', False)}")
    lines.append(f"- can close: {payload.get('can_close')}")
    if payload.get("acceptance_status"):
        lines.append(f"- acceptance: {payload['acceptance_status']}")
    if payload.get("basis_digest"):
        lines.append(f"- basis digest: {payload['basis_digest']}")
    blockers = payload.get("blockers") or []
    if blockers:
        lines.append("- blockers:")
        for blocker in blockers:
            lines.append(f"  - {blocker.get('reason_code')}: {blocker.get('detail')}")
    required = payload.get("required_todo_ids") or []
    if required:
        lines.append(f"- required todos: {len(required)}")
        for todo_id in required:
            satisfied = todo_id in (payload.get("satisfied_todo_ids") or [])
            lines.append(f"  - {todo_id}: {'settled' if satisfied else 'open'}")
    if payload.get("reason_code"):
        lines.append(f"- reason: {payload['reason_code']}")
    if payload.get("completed_at"):
        lines.append(f"- completed at: {payload['completed_at']}")
    operations = payload.get("operations")
    if isinstance(operations, list):
        lines.append(f"- retained operations: {len(operations)}")
        for entry in operations:
            lines.append(f"  - {entry.get('operation_id')}: {entry.get('status')}")
    return "\n".join(lines)


def handle_goal_closeout_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    output_format: Any,
    print_payload: Any,
) -> int | None:
    if args.command != "goal-closeout":
        return None
    try:
        common = {
            "registry_path": registry_path,
            "goal_id": args.goal_id,
            "runtime_root": runtime_root_arg,
        }
        if args.action in {"inspect", "history"}:
            if args.expected_basis_digest or args.owner_principal:
                raise ValueError(
                    f"{args.action} is read-only and accepts neither --owner-principal "
                    "nor --expected-basis-digest"
                )
            payload = (
                inspect_goal_closeout(**common, agent_id=args.agent_id)
                if args.action == "inspect"
                else read_goal_closeout_history(**common, agent_id=args.agent_id)
            )
        elif args.action == "prepare":
            if args.expected_basis_digest:
                raise ValueError("prepare does not accept --expected-basis-digest")
            if args.execute and not args.owner_principal:
                raise ValueError("prepare --execute requires --owner-principal")
            payload = prepare_goal_closeout(
                **common,
                agent_id=args.agent_id,
                owner_principal_id=args.owner_principal,
                operation_id=args.operation_id,
                execute=args.execute,
            )
        else:
            if not args.expected_basis_digest:
                raise ValueError(
                    "confirm requires --expected-basis-digest from a fresh prepare"
                )
            if not args.owner_principal:
                raise ValueError("confirm requires --owner-principal")
            if args.agent_id:
                raise ValueError("confirm never accepts an agent actor")
            payload = confirm_goal_closeout(
                **common,
                expected_basis_digest=args.expected_basis_digest,
                owner_principal_id=args.owner_principal,
                operation_id=args.operation_id,
                execute=args.execute,
            )
        payload = public_goal_closeout(payload)
    except Exception as exc:  # noqa: BLE001 - surfaced as a bounded CLI payload
        payload = {"ok": False, "error": str(exc), "goal_id": args.goal_id}
    print_payload(payload, args.format, render_goal_closeout)
    return 0 if payload.get("ok") else 1

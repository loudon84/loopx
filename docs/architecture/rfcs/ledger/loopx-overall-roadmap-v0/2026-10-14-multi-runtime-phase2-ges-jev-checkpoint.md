# Multi-runtime Phase 2A/2B placement checkpoint (2026-10-14)

Recorded after Phase 1 multi-runtime Worker / model-route / execution-package
delivery. This checkpoint **places** GES skill injection and Jev model
recommendation; it does **not** authorize implementation. Both slices stay
blocked on external contracts named below.

Roadmap owners: **S4** (runtime/host) and **S8** (capabilities / extensions).
Related accepted material: [Project Skill Delivery](../../../../loopx/capabilities/project_skill_delivery/README.md),
[optional semantic assistance / Jev](../../optional-semantic-assistance-jev-v0.md),
and the Phase 1 Coding Worker catalog under `loopx/control_plane/worker_catalog.py`.

## 2A — GES capability injection (blocked on D-01)

| Decision | Choice |
| --- | --- |
| Capability id | `project-skill-delivery` (reuse existing owner; do not invent a parallel skill kernel) |
| Provider id | `loopx-ges` |
| Package path | `packages/loopx-ges/` (extension-delivered, default-off) |
| Surfaces to add first | `cursor` (`CURSOR_HOME/skills` and/or project `.cursor/skills`) and `dsh` (host-native skill root once D-01 names it) |
| Reuse | Existing preview / apply / refuse-overwrite / digest / readback of project-skill-delivery |
| Out of scope until D-01 | Any install into Cursor or DSH, any GES schema parser, any auto-trust of remote skill bytes |

**D-01 (blocking):** GES repository identity, skill package schema, install
safety contract (digest, overwrite policy, privilege boundary), and which host
paths are in-scope. Until that contract is owner-accepted, no `packages/loopx-ges`
code lands.

## 2B — Jev runtime model recommendation (blocked on D-02)

| Decision | Choice |
| --- | --- |
| Capability id | `runtime-model-recommendation` (new caller outcome; not a progress_review alias) |
| Provider package | `packages/loopx-jev-routing/` |
| Transport | Built-in stdio MCP client (pattern reference: `packages/loopx-ark-turn`); LoopX core still owns no ambient MCP client |
| Modes | Dedicated schema for `off` / `shadow` / `enforce` — **do not** reuse progress_review `off`/`shadow` or assist semantics |
| Enforce output | Candidate model / effort **ids only**; every candidate re-enters `loopx/control_plane/turn_driver/worker_route.py` for endpoint capability validation |
| Authority | Recommendation never mutates Goal/Todo authority, never bypasses managed_execution_profile, never auto-applies without the route resolver |

**D-02 (blocking):** MCP service provenance, tool schema, and authentication.
The existing `packages/loopx-jev` HTTP off/shadow progress_review observer remains
a separate product surface; Phase 2B must not collapse routing into that observer.

Existing [Jev RFC](../../optional-semantic-assistance-jev-v0.md) remains the
judgment/assessment research basis. Product adoption for **model routing** is a
separate decision after D-02; RFC intake there does not authorize this provider.

## Explicit non-claims

- Phase 1 Codex / Cursor / DSH Workers do not imply GES or Jev readiness.
- No new shared vocabulary, CLI, Dashboard control, or journal field is added by
  this checkpoint.
- Real Cursor / Codex / DSH account integration remains under D-03 / D-04
  evidence gates from the multi-runtime PRD; absence of those accounts is an
  evidence gap, not a skip-as-pass.

## Next owner action

1. Accept or revise D-01 and D-02 with external contract owners.
2. Open a focused RFC (or amend an existing one) only after those decisions;
   then implement in separate PRs with default-off flags and readback.
3. Keep this ledger entry as the placement source of truth until superseded.

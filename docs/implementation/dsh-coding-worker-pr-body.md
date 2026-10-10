# PR: LoopX DSH Coding Worker v1.0

**Branch** `feature/dsh-coding-worker` → `main`
**Base** `2643d4ff79acfb5ae8730b11c329d7c6287e3262`
**Commits** 13 · **New tests** 122 (+211 existing regression) · **Worktree** clean
**Status** ready for review · **not** self-merged · **not** promoted to the installed release

---

## What this delivers

LoopX can create a Goal from the Web, execute it as governed Turns through the
DSH `0.2.0-rc.2` **headless CLI**, adjudicate the result with an independent
validator, commit the settlement, and continue to the next Todo automatically —
without touching the read-only Steward Chat path.

```
LoopX Web → goal.create(executor_route=governed_turn_worker)
  → Governed Turn Worker Catalog admission (live probe)
  → LoopX grants one Turn
  → dsh --profile headless --json -     (one supervised process, task on stdin)
  → independent Validator → Turn Journal → writeback + quota
```

## Commits

| Commit | Scope |
|---|---|
| `57b02b5df` `a52ed007a` | Phase 0: DSH 0.2 compatibility report, ADR, file-level change list |
| `357f4619b` | Phase 1: CLI host adapter (`dsh_cli_ndjson` / `_process` / `_probe` / `_runner`) |
| `535d36616` `f8e1820c8` | Phase 2: Worker Catalog + `goal.create` dual route + Dashboard executor selector |
| `27a674fa1` `d23bce5fb` | Phase 3: host loop, automatic continuation, AC-09 dependency ruling |
| `bd088d1a4` `9c6ccede0` | Phase 3 remainder: typed stops, AC-10, exact-identity settlement, run readback |
| `c224dc598` | Phase 4: capability dark by default, rollback, ops manual |
| `e8c571009` | fix: decide a gate as its authoritative bound actor |
| latest | feat: render governed Turn run history (AC-15 UI) |

## Design decisions worth reviewing

1. **A separate Worker Catalog, not the Chat endpoint registry.** The custom
   endpoint surface is structurally restricted: `adapter_kind` must be `"acp"`
   and `trust_scope` must be `"read_only"` (`loopx/chat_endpoints.py:111-121`).
   A write-capable worker cannot be represented there without weakening the
   read-only guarantee the managed Steward depends on. The catalog is additive;
   `deepseek_harness_segment` keeps `tool_calls=false` / `read_only` unchanged.
2. **The 0.2 CLI is driven as a subprocess.** The installed argv surface is only
   `--json`, `--session-id` and the task; model, provider, reasoning effort and
   max tokens are **not** argv-settable and are recorded as
   `unsupported_argv_controls` rather than pretended. `output_token_limit` is
   therefore **not enforced** on this route.
3. **Completion is `exit == 0 AND turn_end.reason.kind == "completed"`.**
   `--json` is a bounded projection, never evidence.
4. **A prepared effect is settled by its exact Turn identity**, never by
   re-running a host that already produced side effects (`host_invoked: false`
   on the resume path).
5. **Dependency gates use LoopX's native primitives** — a `blocked` dependent
   plus `--successor-todo-id` at the predecessor's completion. No new gate
   subsystem (ADR-07).

## Verification

```bash
env -u DSH_HOME python3 -m pytest \
  tests/test_dsh_cli_ndjson.py tests/test_dsh_cli_runner.py tests/test_dsh_cli_probe.py \
  tests/test_worker_catalog.py tests/test_governed_turn_loop.py tests/test_worker_run_readback.py -q
# 122 passed

env -u DSH_HOME python3 -m pytest \
  tests/test_dsh_goal_mode.py tests/test_chat_dsh_adapter.py tests/test_loopx_turn_driver.py -q
# 211 passed
```

`env -u DSH_HOME` is required: `resolve_dsh_home` reads the ambient `DSH_HOME`
and several pre-existing tests assert a workspace-local default.

**Real end-to-end evidence** (isolated testbed, never the product repo):

- Two dependent Todos, two committed Turns, full phase sequence
  `host_execute, typed_result, validation, durable_writeback, quota_spend,
  scheduler_apply, scheduler_ack`; both validators passed; Turn 2 admitted by
  LoopX after Turn 1 settled with **no intervening human message**.
- A paused goal and an open user gate each stop the loop with
  `host_invocations=0` (AC-10).
- Crash recovery: settlement resumed the **existing** Turn identity with
  `host_invoked: False` and no double quota spend.

Details: `docs/implementation/dsh-coding-worker-test-report.md`.

## Known gaps — please weigh these before approving

1. **AC-15 is not browser-verified.** The card, its zod schema and its route are
   verified (tsc, contract test, compiled into the bundle, route exercised
   against three real Turns), but no browser render was performed: no playwright
   browser is installed, and after the fixture cleanup no active Goal has Turn
   journals left, so the card currently shows its empty state everywhere.
2. **A quota-exhaustion state could not be reproduced locally.** The whole
   refusal class is covered by unit tests; only `paused` and `gate` are proven
   end-to-end.
3. **AC-12 is partial.** No-shell invocation, stdin-only prompts, workspace
   mismatch refusal and cross-Goal session refusal are covered; out-of-scope
   writes and dangerous-command refusal are not.
4. **AC-17 (session resume positive path) is unverified.** `--session-id`
   defaults to fresh; cross-process adoption has only its negative path proven.
5. **An open `autonomous_replan_required` obligation remains on the development
   Goal.** It needs a typed semantic delta in LoopX's accepted-surface
   vocabulary; authored vision and typed progress observations were both
   rejected. The code is unaffected; this is bookkeeping.

## Rollback

The capability ships **dark**. `unset LOOPX_DSH_CODING_WORKER` refuses new
worker-route Goals (typed error) while the Chat agent route is provably
unaffected. Full procedure including in-flight Turn reconciliation:
`docs/integrations/dsh-coding-worker.md` §8.

## Not done, deliberately

- No merge to `main`, no promotion to the installed release snapshot, no push.
  Per the PRD these require review plus explicit owner approval.

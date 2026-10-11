/** Goal Closeout authority: canonical head, CAS commit, owner gate and history.
 *
 * The acceptance contract used by these cases is produced by the real
 * acceptance authority (`configureGoalAcceptance` +
 * `commitGoalAcceptanceVerification`), never by a hand-written "passed" flag, so
 * AC-03/AC-04 cannot be satisfied by a fabricated receipt.
 */
import assert from "node:assert/strict";
import {randomUUID} from "node:crypto";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test, {type TestContext} from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {sqliteRuntimeIdentity} from "../../loopx/control_plane/coordination/sqlite_runtime.ts";
import {prepareCoordinationProjectionCommit} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {createEffectRuntimeHandlers} from "../../loopx/control_plane/effect_runtime_handlers.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";
import {commitGoalAcceptanceVerification, configureGoalAcceptance,
  inspectGoalAcceptance} from "../../loopx/control_plane/goals/acceptance_authority.ts";
import {projectGoalCloseout, readGoalCloseout} from "../../loopx/control_plane/goals/closeout_contract.ts";
import {confirmGoalCloseout, inspectGoalCloseout, prepareGoalCloseout,
  readGoalCloseoutHistory} from "../../loopx/control_plane/goals/closeout_authority.ts";

const goal = "goal-closeout-test";
const PRINCIPAL = "owner-principal-closeout";
const GOAL_REF = {goal_id: goal, goal_instance_id: `ginst_${"a".repeat(32)}`};

function todo(todoId: string, extra: JsonObject = {}): JsonObject {
  return {todo_id: todoId, role: "agent", status: "open", done: false, archive_state: "active",
    text: `Implement ${todoId}`, task_class: "advancement_task", action_kind: "implement", ...extra};
}
const DONE = {status: "done", done: true, completion_continuation: "active_goal"};

function document(): JsonObject {
  return {scope: {kind: "all_advancement"}, objective: "Deliver independently validated work",
    non_goals: ["Grant additional permissions"],
    criteria: [
      {id: "prerequisite", description: "Prerequisite passes", validation_argv: [process.execPath, "-e", "process.exit(0)"]},
      {id: "outcome", description: "Outcome passes", validation_argv: [process.execPath, "-e", "process.exit(0)"]},
    ],
    bindings: [{todo_id: "todo_first", criterion_ids: ["prerequisite"]},
      {todo_id: "todo_second", criterion_ids: ["outcome"]}]};
}

async function seed(store: AuthorityStore, projection: JsonObject): Promise<void> {
  assert.equal((await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
    events: [], receipts: [], next_projection: projection})).status, "applied");
}
async function head(store: AuthorityStore) {
  const result = await store.loadAuthority();
  assert.equal(result.status, "loaded");
  if (result.status !== "loaded") throw new Error("test head unavailable");
  return result;
}
/** Configure and independently verify through the real acceptance authority. */
async function acceptWork(store: AuthorityStore): Promise<void> {
  const before = await head(store);
  const configured = await configureGoalAcceptance(store, {
    goal_id: goal, actor_agent_id: null, operation_id: randomUUID(), disable: false,
    expected_provider_revision: before.provider_revision, document: document()});
  assert.equal(configured.status, "applied", JSON.stringify(configured));
  const basis = await inspectGoalAcceptance(store, goal);
  const verified = await commitGoalAcceptanceVerification(store, {
    goal_id: goal, operation_id: randomUUID(), actor_agent_id: null,
    expected_provider_revision: basis.provider_revision,
    revision: basis.revision, contract_digest: basis.contract_digest,
    results: [{criterion_id: "outcome", passed: true, exit_code: 0},
      {criterion_id: "prerequisite", passed: true, exit_code: 0}]});
  assert.equal(verified.status, "applied", JSON.stringify(verified));
}
function prepareRequest(current: {provider_revision: string}, extra: JsonObject = {}): JsonObject {
  return {goal_id: goal, operation_id: randomUUID(), actor_agent_id: null,
    expected_provider_revision: current.provider_revision, owner_principal_id: PRINCIPAL,
    goal_ref: GOAL_REF, ...extra};
}

const providers = ["file", "sqlite"] as const;
const sqliteQualified = sqliteRuntimeIdentity().sqlite_authority_qualified === true;
async function fixture(t: TestContext, provider: typeof providers[number]): Promise<AuthorityStore> {
  const dir = await mkdtemp(join(tmpdir(), "goal-closeout-"));
  t.after(() => rm(dir, {recursive: true, force: true}));
  return provider === "file" ? new FileAuthorityStore(dir, goal) : new SqliteAuthorityStore(dir, goal);
}

for (const provider of providers) {
  const options = {skip: provider === "sqlite" && !sqliteQualified};

  test(`${provider}: an open required Todo blocks Closeout`, options, async t => {
    const store = await fixture(t, provider);
    await seed(store, authorityProjectionFixture(goal, [todo("todo_first", DONE), todo("todo_second")]));
    await acceptWork(store); // acceptance passes; the open Todo still blocks
    const current = await head(store);
    const prepared = await prepareGoalCloseout(store, prepareRequest(current));
    assert.equal(prepared.status, "failed");
    assert.equal(prepared.reason_code, "closeout_open_required_todos");
    assert.deepEqual(prepared.required_todo_ids, ["todo_first", "todo_second"]);
    assert.deepEqual(prepared.satisfied_todo_ids, ["todo_first"]);
    // AC-02: hidden is irrelevant — the canonical head is the only input.
    assert.equal(readGoalCloseout((await head(store)).head, goal), null);
  });

  test(`${provider}: done Todos without a configured acceptance contract are refused`, options, async t => {
    const store = await fixture(t, provider);
    await seed(store, authorityProjectionFixture(goal,
      [todo("todo_first", DONE), todo("todo_second", DONE)]));
    const current = await head(store);
    const prepared = await prepareGoalCloseout(store, prepareRequest(current));
    assert.equal(prepared.status, "failed");
    assert.equal(prepared.reason_code, "closeout_acceptance_not_configured");
    assert.equal(prepared.acceptance_status, "disabled");
    assert.equal(readGoalCloseout((await head(store)).head, goal), null,
      "a refused prepare never writes closeout state");
  });

  test(`${provider}: a failed independent verification blocks Closeout`, options, async (t: TestContext) => {
    const store = await fixture(t, provider);
    await seed(store, authorityProjectionFixture(goal,
      [todo("todo_first", DONE), todo("todo_second", DONE)]));
    const before = await head(store);
    await configureGoalAcceptance(store, {goal_id: goal, actor_agent_id: null,
      operation_id: randomUUID(), disable: false,
      expected_provider_revision: before.provider_revision, document: document()});
    const basis = await inspectGoalAcceptance(store, goal);
    const verified = await commitGoalAcceptanceVerification(store, {goal_id: goal,
      operation_id: randomUUID(), actor_agent_id: null,
      expected_provider_revision: basis.provider_revision, revision: basis.revision,
      contract_digest: basis.contract_digest,
      results: [{criterion_id: "outcome", passed: false, exit_code: 1},
        {criterion_id: "prerequisite", passed: true, exit_code: 0}]});
    assert.equal(verified.status, "applied");
    const prepared = await prepareGoalCloseout(store, prepareRequest(await head(store)));
    assert.equal(prepared.status, "failed");
    assert.equal(prepared.reason_code, "closeout_acceptance_failed");
    assert.equal(prepared.acceptance_status, "failed");
  });

  test(`${provider}: work changed after verification makes the basis stale`, options, async t => {
    const store = await fixture(t, provider);
    await seed(store, authorityProjectionFixture(goal,
      [todo("todo_first", DONE), todo("todo_second", DONE)]));
    await acceptWork(store);
    // AC-04: the delivered work moves after the independent verification ran.
    const current = await head(store);
    const previous = (current.head.todos as JsonObject[]).find(row => row.todo_id === "todo_first");
    assert.ok(previous);
    const updated = await store.commitAuthority(prepareCoordinationProjectionCommit({goal_id: goal,
      operation_id: randomUUID(), expected_provider_revision: current.provider_revision,
      projection: current.head,
      mutations: [{kind: "todo_upsert", todo: {...previous, text: "Different delivered work"}}]}));
    assert.equal(updated.status, "applied");
    const prepared = await prepareGoalCloseout(store, prepareRequest(await head(store)));
    assert.equal(prepared.status, "failed");
    assert.equal(prepared.reason_code, "closeout_acceptance_stale");
  });

  test(`${provider}: accepted independent verification admits owner confirmation`, options, async t => {
    const store = await fixture(t, provider);
    await seed(store, authorityProjectionFixture(goal,
      [todo("todo_first", DONE), todo("todo_second", DONE)]));
    await acceptWork(store);
    const before = await head(store);
    const request = prepareRequest(before);

    const preview = await prepareGoalCloseout(store, {...request, dry_run: true});
    assert.equal(preview.status, "planned");
    assert.equal(preview.changed, false);
    assert.deepEqual(await head(store), before, "dry-run cannot mutate the head");

    const applied = await prepareGoalCloseout(store, request);
    assert.equal(applied.status, "applied", JSON.stringify(applied));
    const state = readGoalCloseout((await head(store)).head, goal);
    assert.equal(state?.state, "awaiting_owner_confirmation");
    assert.equal(state?.owner_principal_id, PRINCIPAL);
    assert.ok(state?.acceptance_receipt_id);
  });

  test(`${provider}: an agent actor can never confirm the Goal`, options, async t => {
    const store = await fixture(t, provider);
    await seed(store, authorityProjectionFixture(goal,
      [todo("todo_first", DONE), todo("todo_second", DONE)]));
    await acceptWork(store);
    const prepared = await prepareGoalCloseout(store, prepareRequest(await head(store)));
    assert.equal(prepared.status, "applied");
    const basisDigest = String(prepared.basis_digest ?? "");
    const agentAttempt = await confirmGoalCloseout(store, {
      goal_id: goal, operation_id: randomUUID(), actor_agent_id: "dsh-coding-worker-01",
      owner_principal_id: PRINCIPAL, expected_basis_digest: basisDigest});
    assert.equal(agentAttempt.status, "failed");
    assert.equal(agentAttempt.reason_code, "closeout_owner_required");
    assert.equal(readGoalCloseout((await head(store)).head, goal)?.state,
      "awaiting_owner_confirmation", "an agent confirm cannot advance the state");
  });

  test(`${provider}: a stale basis digest is refused before any write`, options, async t => {
    const store = await fixture(t, provider);
    await seed(store, authorityProjectionFixture(goal,
      [todo("todo_first", DONE), todo("todo_second", DONE)]));
    await acceptWork(store);
    await prepareGoalCloseout(store, prepareRequest(await head(store)));
    const before = await head(store);
    const stale = await confirmGoalCloseout(store, {
      goal_id: goal, operation_id: randomUUID(), actor_agent_id: null,
      owner_principal_id: PRINCIPAL, expected_basis_digest: "0".repeat(64)});
    assert.equal(stale.status, "failed");
    assert.equal(stale.reason_code, "closeout_basis_stale");
    assert.deepEqual(await head(store), before);
  });

  test(`${provider}: owner confirmation commits completed atomically and replays`, options, async t => {
    const store = await fixture(t, provider);
    await seed(store, authorityProjectionFixture(goal,
      [todo("todo_first", DONE), todo("todo_second", DONE)]));
    await acceptWork(store);
    const prepared = await prepareGoalCloseout(store, prepareRequest(await head(store)));
    assert.equal(prepared.status, "applied");
    const operationId = randomUUID();
    const confirmRequest = {
      goal_id: goal, operation_id: operationId, actor_agent_id: null, goal_ref: GOAL_REF,
      owner_principal_id: PRINCIPAL, expected_basis_digest: String(prepared.basis_digest ?? ""),
    };
    const confirmed = await confirmGoalCloseout(store, confirmRequest);
    assert.equal(confirmed.status, "applied", JSON.stringify(confirmed));
    const state = readGoalCloseout((await head(store)).head, goal);
    assert.equal(state?.state, "completed");
    assert.equal(state?.owner_confirmation !== null, true);
    assert.ok(state?.completion_receipt_id);
    assert.ok(state?.completed_at);
    assert.equal(projectGoalCloseout((await head(store)).head, goal).completed, true);

    // AC-07: the same operation id replays the original receipt.
    const replay = await confirmGoalCloseout(store, confirmRequest);
    assert.equal(replay.status, "replayed");
    assert.equal(replay.completion_receipt_id, confirmed.completion_receipt_id);

    // AC-09/AC-11: completed is terminal for prepare.
    const again = await prepareGoalCloseout(store, prepareRequest(await head(store)));
    assert.equal(again.status, "failed");
    assert.equal(again.reason_code, "closeout_already_completed");

    // AC-08/history: the committed operations stay readable.
    const history = await readGoalCloseoutHistory(store, goal);
    assert.equal(history.status, "loaded");
    assert.equal((history.operations as JsonObject[]).length, 2);
  });

  test(`${provider}: an active lease blocks Closeout`, options, async t => {
    const store = await fixture(t, provider);
    await seed(store, authorityProjectionFixture(goal,
      [todo("todo_first", DONE), todo("todo_second", DONE)],
      [{todo_id: "todo_first", status: "active", owner: "dsh-coding-worker-01", version: 3}]));
    await acceptWork(store);
    const prepared = await prepareGoalCloseout(store, prepareRequest(await head(store)));
    assert.equal(prepared.status, "failed");
    assert.equal(prepared.reason_code, "closeout_lease_conflict");
  });

  test(`${provider}: a continuous monitor must be retired or handed over`, options, async t => {
    const store = await fixture(t, provider);
    await seed(store, authorityProjectionFixture(goal, [todo("todo_first", DONE), todo("todo_second", DONE),
      todo("todo_monitor", {task_class: "continuous_monitor"})]));
    await acceptWork(store);
    const prepared = await prepareGoalCloseout(store, prepareRequest(await head(store)));
    assert.equal(prepared.status, "failed");
    assert.equal(prepared.reason_code, "closeout_monitor_not_retired");
  });

  test(`${provider}: an unsettled Turn and a vision close both fail closed`, options, async t => {
    const store = await fixture(t, provider);
    await seed(store, authorityProjectionFixture(goal,
      [todo("todo_first", DONE), todo("todo_second", DONE)], [],
      "native", {unsettled_turns: [{turn_instance_id: "turn-1", state: "settling"}]}));
    await acceptWork(store);
    const prepared = await prepareGoalCloseout(store, prepareRequest(await head(store)));
    assert.equal(prepared.status, "failed");
    assert.equal(prepared.reason_code, "closeout_unsettled_turn");

    // AC-18: a vision close is a different lifecycle and never completes a Goal.
    const second = await fixture(t, provider);
    await seed(second, authorityProjectionFixture(goal,
      [todo("todo_first", DONE), todo("todo_second", DONE)], [],
      "native", {goal_vision: {state: "vision_closed"}}));
    await acceptWork(second);
    const vision = await prepareGoalCloseout(second, prepareRequest(await head(second)));
    assert.equal(vision.status, "applied");
    assert.equal(readGoalCloseout((await head(second)).head, goal)?.state,
      "awaiting_owner_confirmation", "a vision close never produces completed");
  });

  test(`${provider}: inspect reports blockers without writing`, options, async t => {
    const store = await fixture(t, provider);
    await seed(store, authorityProjectionFixture(goal, [todo("todo_first"), todo("todo_second", DONE)]));
    const before = await head(store);
    const inspected = await inspectGoalCloseout(store, goal);
    assert.equal(inspected.status, "loaded");
    assert.equal(inspected.state, "closeout_blocked");
    assert.equal(inspected.can_close, false);
    assert.deepEqual((inspected.blockers as JsonObject[]).map(row => row.reason_code),
      ["closeout_acceptance_not_configured", "closeout_open_required_todos"].sort());
    assert.deepEqual(await head(store), before, "inspect is read-only");
  });
}

test("the runtime boundary registers exactly the four canonical closeout RPCs", () => {
  const handlers = createEffectRuntimeHandlers({
    fingerprint: "test-fingerprint", requestShutdown: () => undefined});
  for (const method of ["goal.closeout.inspect", "goal.closeout.prepare",
    "goal.closeout.confirm", "goal.closeout.history"]) {
    assert.equal(handlers.has(method), true, `${method} must be registered`);
  }
  // AC-20: no side door that completes a Goal without the owner contract.
  for (const bypass of ["goal.closeout.archive", "goal.closeout.force",
    "goal.closeout.complete", "goal.closeout.set_state"]) {
    assert.equal(handlers.has(bypass), false, `${bypass} must not exist`);
  }
});

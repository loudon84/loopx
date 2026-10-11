/** Canonical Goal Closeout authority.
 *
 * Inspect/Prepare/Confirm/History over the Goal's existing authority head. All
 * mutations ride the same CAS commit and idempotent operation receipt the
 * acceptance authority uses, so a Closeout commit is atomic with its audit
 * event and never becomes a free-standing completion flag.
 *
 * Separation of duty (D05): Prepare may be requested by an owner or trusted
 * control plane; Confirm additionally requires an owner principal and refuses
 * every Agent actor. A CLI flag is not proof of identity (R4), so the local
 * entrypoint only records the principal it is given and the Web/trusted host
 * remains the authority that authenticates the real owner.
 */
import {isAbsolute} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import type {AuthorityStore, AuthorityStoreHead} from "../coordination/authority_store.ts";
import {authorityStoreSourceAuthority} from "../coordination/authority_store.ts";
import {canonicalAuthorityObject, canonicalAuthoritySha256,
  requireAuthorityStoreId} from "../coordination/authority_store_codec.ts";
import {CoordinationCommandReceipt} from "../coordination/command_receipt.ts";
import {withCanonicalWriter} from "../coordination/local_authority_write.ts";
import {openLocalAuthorityStore, localAuthorityOpenFailure} from "../coordination/local_authority_provider.ts";
import {projectGoalAcceptance} from "./acceptance_contract.ts";
import {
  GOAL_CLOSEOUT_HISTORY_SCHEMA, GOAL_CLOSEOUT_RECEIPT_SCHEMA, GOAL_CLOSEOUT_RESULT_SCHEMA,
  assertCloseoutInvariants, closeoutBasisDigest, closeoutKeys, closeoutRequire, closeoutText,
  evaluateGoalCloseout, normalizeGoalCloseout, projectGoalCloseout, readGoalCloseout,
  type GoalCloseoutBasis, type GoalCloseoutStateRecord,
} from "./closeout_contract.ts";

const RESULT_SCHEMA = GOAL_CLOSEOUT_RESULT_SCHEMA;
const RECEIPT_SCHEMA = GOAL_CLOSEOUT_RECEIPT_SCHEMA;
const REQUEST_FIELDS = ["goal_id", "operation_id", "actor_agent_id", "expected_provider_revision"];
const LOCAL_FIELDS = ["runtime_root", "dry_run"];

function request(value: unknown, extra: readonly string[]): JsonObject {
  const record = canonicalAuthorityObject(value, "goal closeout request");
  closeoutKeys(record, [...REQUEST_FIELDS, ...LOCAL_FIELDS, ...extra], "goal closeout request");
  for (const field of ["goal_id", "operation_id"]) {
    requireAuthorityStoreId(record[field], field);
  }
  closeoutRequire(record.dry_run === undefined || typeof record.dry_run === "boolean",
    "goal closeout dry_run must be boolean");
  if (record.actor_agent_id !== null && record.actor_agent_id !== undefined) {
    requireAuthorityStoreId(record.actor_agent_id, "actor_agent_id");
  }
  if (record.expected_provider_revision !== null && record.expected_provider_revision !== undefined) {
    closeoutText(record.expected_provider_revision, "expected_provider_revision");
  }
  return record;
}

function source(store: AuthorityStore, result: JsonObject): JsonObject {
  return {...result, source_authority: authorityStoreSourceAuthority(store),
    decision_read_from_provider: true, legacy_fallback_used: false};
}

function failure(reasonCode: string, reason: string): JsonObject & {schema_version: typeof RESULT_SCHEMA} {
  return {schema_version: RESULT_SCHEMA, status: "failed", changed: false,
    reason_code: reasonCode, reason};
}

/** Blocker detail rides along the typed failure without widening the receipt contract. */
function failureWith(reasonCode: string, reason: string, extra: JsonObject): JsonObject {
  return {...failure(reasonCode, reason), ...extra};
}

function receiptFor(record: JsonObject, kind: "prepare" | "confirm") {
  // Transport location and dry-run are not operation content; everything else,
  // including the expected CAS and the supplied principal, is bound.
  const content = Object.fromEntries(Object.entries(record)
    .filter(([key]) => !LOCAL_FIELDS.includes(key)));
  const identity = {
    schema_version: RECEIPT_SCHEMA,
    operation_id: String(record.operation_id),
    goal_id: String(record.goal_id),
    request_sha256: canonicalAuthoritySha256({kind, ...content}),
  };
  const receipt = new CoordinationCommandReceipt({
    result_schema: RESULT_SCHEMA,
    identity,
    failure,
    decode: (original: JsonObject) => ({
      fields: canonicalAuthorityObject(original.result, "goal closeout operation result"),
      changed: true,
    }),
  });
  return {identity, receipt};
}

function result(goalId: string, request_: JsonObject, head: AuthorityStoreHead,
  closeout: GoalCloseoutStateRecord | null, extra: JsonObject = {}): JsonObject {
  return {
    goal_id: goalId,
    operation_id: request_.operation_id,
    goal_closeout: projectGoalCloseout(
      closeout === null ? head.head : {...head.head, goal_closeout: closeout as unknown as JsonObject},
      goalId),
    ...extra,
  };
}

/* ------------------------------------------------------------------ *
 * Inspect                                                            *
 * ------------------------------------------------------------------ */

export async function inspectGoalCloseout(
  store: AuthorityStore,
  goalId: string,
): Promise<JsonObject> {
  const head = await store.loadAuthority();
  if (head.status !== "loaded") return source(store, {...head});
  const state = readGoalCloseout(head.head, goalId);
  const evaluation = evaluateGoalCloseout(head.head, goalId, head.provider_revision);
  const acceptance = projectGoalAcceptance(head.head, goalId);
  const blocked = state !== null && state.state === "completed"
    ? false : evaluation.blockers.length > 0;
  return source(store, {
    status: "loaded",
    goal_id: goalId,
    provider_revision: head.provider_revision,
    state: state !== null && state.state === "completed" ? "completed"
      : blocked ? "closeout_blocked" : evaluation.state,
    can_close: !blocked && state?.state !== "completed",
    blockers: evaluation.blockers,
    required_todo_ids: evaluation.required_todo_ids,
    satisfied_todo_ids: evaluation.satisfied_todo_ids,
    acceptance_status: evaluation.acceptance_status,
    acceptance_enabled: acceptance.enabled === true,
    basis: evaluation.basis as unknown as JsonObject,
    basis_digest: closeoutBasisDigest(evaluation.basis),
    basis_digest_matches_state: state === null
      ? null
      : closeoutBasisDigest(state.basis) === closeoutBasisDigest(evaluation.basis),
    owner_confirmation_required: state?.state === "awaiting_owner_confirmation",
    owner_principal_bound: state !== null && state.owner_principal_id !== null,
    goal_closeout: projectGoalCloseout(head.head, goalId),
  });
}

/* ------------------------------------------------------------------ *
 * Prepare                                                            *
 * ------------------------------------------------------------------ */

export async function prepareGoalCloseout(
  store: AuthorityStore,
  value: JsonObject,
): Promise<JsonObject> {
  const record = request(value, ["owner_principal_id", "goal_ref"]);
  const goalId = String(record.goal_id);
  const command = receiptFor(record, "prepare");
  const replay = await command.receipt.read(store);
  if (replay) return source(store, replay);

  const observation = await command.receipt.observe(store);
  if (observation.kind === "receipt") return source(store, observation.result);
  if (observation.authority.status !== "loaded") return source(store, {...observation.authority});
  const head = observation.authority;
  if (record.expected_provider_revision !== undefined && record.expected_provider_revision !== null
      && String(record.expected_provider_revision) !== head.provider_revision) {
    return source(store, failure("closeout_basis_stale",
      "the Goal authority moved after the preview was read"));
  }
  const current = readGoalCloseout(head.head, goalId);
  if (current !== null && current.state === "completed") {
    return source(store, failure("closeout_already_completed",
      "a completed Goal closeout cannot be prepared again"));
  }
  const requestedGoalRef = record.goal_ref === undefined || record.goal_ref === null
    ? null : canonicalAuthorityObject(record.goal_ref, "closeout goal_ref");
  if (current !== null && current.goal_ref !== null && requestedGoalRef !== null
      && (current.goal_ref.goal_id !== String(requestedGoalRef.goal_id)
        || current.goal_ref.goal_instance_id !== String(requestedGoalRef.goal_instance_id))) {
    return source(store, failure("closeout_goal_ref_mismatch",
      "goal closeout belongs to another Goal instance"));
  }
  const suppliedPrincipal = record.owner_principal_id === undefined || record.owner_principal_id === null
    ? null : closeoutText(record.owner_principal_id, "owner_principal_id");
  if (current !== null && current.owner_principal_id !== null && suppliedPrincipal !== null
      && current.owner_principal_id !== suppliedPrincipal) {
    return source(store, failure("closeout_owner_mismatch",
      "the supplied owner principal is not the Goal's bound owner"));
  }

  const evaluation = evaluateGoalCloseout(head.head, goalId, head.provider_revision);
  const planned = result(goalId, record, head, current, {
    state: evaluation.state,
    can_close: evaluation.can_close,
    blockers: evaluation.blockers,
    required_todo_ids: evaluation.required_todo_ids,
    satisfied_todo_ids: evaluation.satisfied_todo_ids,
    acceptance_status: evaluation.acceptance_status,
    basis: evaluation.basis as unknown as JsonObject,
    basis_digest: closeoutBasisDigest(evaluation.basis),
  });
  if (!evaluation.can_close) {
    return source(store, failureWith(
      String(evaluation.blockers[0]?.reason_code ?? "closeout_acceptance_failed"),
      evaluation.blockers.map((blocker) => `${blocker.reason_code}:${blocker.detail}`).join(" | "),
      {...planned, status: "failed"}));
  }
  if (record.dry_run === true) {
    return source(store, {status: "planned", dry_run: true, changed: false,
      provider_revision: head.provider_revision, ...planned});
  }

  const acceptance = projectGoalAcceptance(head.head, goalId);
  const verification = acceptance.verification as JsonObject | undefined;
  const acceptanceReceiptId = verification !== undefined
    && typeof verification.operation_id === "string" && verification.operation_id.length > 0
    ? verification.operation_id
    : canonicalAuthoritySha256(acceptance);
  const next = normalizeGoalCloseout({
    schema_version: "loopx_goal_closeout_v1",
    goal_ref: current?.goal_ref ?? requestedGoalRef,
    policy: "strict_v1",
    state: "awaiting_owner_confirmation",
    owner_principal_id: current?.owner_principal_id ?? suppliedPrincipal,
    closeout_revision: (current?.closeout_revision ?? 0) + 1,
    basis: evaluation.basis as unknown as JsonObject,
    acceptance_receipt_id: acceptanceReceiptId,
    owner_confirmation: null,
    completed_at: null,
    completion_receipt_id: null,
    closeout_operation_ids: [...(current?.closeout_operation_ids ?? []),
      String(record.operation_id)].slice(-64),
  });
  const committed = await command.receipt.commit(store, {
    operation_id: String(record.operation_id),
    expected_provider_revision: head.provider_revision,
    next_projection: {...head.head, goal_closeout: next as unknown as JsonObject},
    events: [{
      schema_version: RECEIPT_SCHEMA,
      kind: "goal_closeout_prepare",
      goal_id: goalId,
      operation_id: record.operation_id,
      state: next.state,
      closeout_revision: next.closeout_revision,
      acceptance_receipt_id: acceptanceReceiptId,
    }],
    receipts: [{...command.identity, result: {
      goal_id: goalId,
      operation_id: record.operation_id,
      state: next.state,
      closeout_revision: next.closeout_revision,
      can_close: true,
      blockers: [],
      basis: evaluation.basis as unknown as JsonObject,
      basis_digest: closeoutBasisDigest(evaluation.basis),
      acceptance_receipt_id: acceptanceReceiptId,
    }}],
  });
  return source(store, committed);
}

/* ------------------------------------------------------------------ *
 * Confirm                                                            *
 * ------------------------------------------------------------------ */

export async function confirmGoalCloseout(
  store: AuthorityStore,
  value: JsonObject,
): Promise<JsonObject> {
  const record = request(value, ["owner_principal_id", "expected_basis_digest", "goal_ref"]);
  const goalId = String(record.goal_id);
  // Duty separation: an Agent/Steward/Hermes actor can request, never confirm.
  if (record.actor_agent_id !== null && record.actor_agent_id !== undefined) {
    return source(store, failure("closeout_owner_required",
      "goal closeout confirmation requires an owner principal, not an agent actor"));
  }
  const principal = closeoutText(record.owner_principal_id, "owner_principal_id");
  const expectedDigest = closeoutText(record.expected_basis_digest, "expected_basis_digest", 64);
  const command = receiptFor(record, "confirm");
  const replay = await command.receipt.read(store);
  if (replay) return source(store, replay);

  const observation = await command.receipt.observe(store);
  if (observation.kind === "receipt") return source(store, observation.result);
  if (observation.authority.status !== "loaded") return source(store, {...observation.authority});
  const head = observation.authority;

  const current = readGoalCloseout(head.head, goalId);
  if (current === null) {
    return source(store, failure("closeout_state_mismatch",
      "no prepared Goal closeout exists for this Goal"));
  }
  if (current.state === "completed") {
    return source(store, failure("closeout_already_completed",
      "the Goal is already completed"));
  }
  if (current.state !== "awaiting_owner_confirmation") {
    return source(store, failure("closeout_state_mismatch",
      `goal closeout is ${current.state}, not awaiting owner confirmation`));
  }
  if (current.owner_principal_id !== null && current.owner_principal_id !== principal) {
    return source(store, failure("closeout_owner_mismatch",
      "the supplied owner principal is not the Goal's bound owner"));
  }
  const requestedGoalRef = record.goal_ref === undefined || record.goal_ref === null
    ? null : canonicalAuthorityObject(record.goal_ref, "closeout goal_ref");
  if (requestedGoalRef !== null && (current.goal_ref === null
      || current.goal_ref.goal_id !== String(requestedGoalRef.goal_id)
      || current.goal_ref.goal_instance_id !== String(requestedGoalRef.goal_instance_id))) {
    return source(store, failure("closeout_goal_ref_mismatch",
      "the supplied goal_ref is not the prepared Goal instance"));
  }
  if (closeoutBasisDigest(current.basis) !== expectedDigest) {
    return source(store, failure("closeout_basis_stale",
      "the confirmation preview no longer matches the prepared basis"));
  }
  // Freeze check: re-derive every blocker on the current revision.
  const evaluation = evaluateGoalCloseout(head.head, goalId, head.provider_revision);
  if (!evaluation.can_close) {
    return source(store, failureWith("closeout_basis_stale",
      evaluation.blockers.map((blocker) => `${blocker.reason_code}:${blocker.detail}`).join(" | "),
      {blockers: evaluation.blockers, basis_digest: closeoutBasisDigest(evaluation.basis)}));
  }
  if (record.dry_run === true) {
    return source(store, {status: "planned", dry_run: true, changed: false,
      provider_revision: head.provider_revision, ...result(goalId, record, head, current, {
        state: "completed",
        can_close: true,
        basis_digest: closeoutBasisDigest(evaluation.basis),
      })});
  }

  const completionReceiptId = `gcc_${canonicalAuthoritySha256({
    goal_id: goalId, operation_id: record.operation_id, principal, basis: evaluation.basis,
  })}`;
  const next = normalizeGoalCloseout({
    ...(current as unknown as JsonObject),
    state: "completed",
    closeout_revision: current.closeout_revision + 1,
    basis: evaluation.basis as unknown as JsonObject,
    owner_confirmation: {
      principal_id: principal,
      operation_id: String(record.operation_id),
      confirmed_at: new Date().toISOString(),
      basis_digest: closeoutBasisDigest(evaluation.basis),
    },
    completed_at: new Date().toISOString(),
    completion_receipt_id: completionReceiptId,
    closeout_operation_ids: [...current.closeout_operation_ids,
      String(record.operation_id)].slice(-64),
  });
  assertCloseoutInvariants(next);
  const committed = await command.receipt.commit(store, {
    operation_id: String(record.operation_id),
    expected_provider_revision: head.provider_revision,
    next_projection: {...head.head, goal_closeout: next as unknown as JsonObject},
    events: [{
      schema_version: RECEIPT_SCHEMA,
      kind: "goal_closeout_confirm",
      goal_id: goalId,
      operation_id: record.operation_id,
      state: "completed",
      owner_principal_id: principal,
      completion_receipt_id: completionReceiptId,
    }],
    receipts: [{...command.identity, result: {
      goal_id: goalId,
      operation_id: record.operation_id,
      state: "completed",
      completion_receipt_id: completionReceiptId,
      owner_principal_id: principal,
    }}],
  });
  return source(store, committed);
}

/* ------------------------------------------------------------------ *
 * History                                                            *
 * ------------------------------------------------------------------ */

export async function readGoalCloseoutHistory(
  store: AuthorityStore,
  goalId: string,
): Promise<JsonObject> {
  const head = await store.loadAuthority();
  if (head.status !== "loaded") return source(store, {...head});
  const state = readGoalCloseout(head.head, goalId);
  const entries: JsonObject[] = [];
  for (const operationId of state?.closeout_operation_ids ?? []) {
    const receipt = await store.readReceipt(operationId);
    entries.push({
      operation_id: operationId,
      status: receipt.status,
      provider_revision: receipt.status === "found" ? receipt.provider_revision : null,
    });
  }
  return source(store, {
    schema_version: GOAL_CLOSEOUT_HISTORY_SCHEMA,
    status: "loaded",
    goal_id: goalId,
    provider_revision: head.provider_revision,
    goal_closeout: projectGoalCloseout(head.head, goalId),
    operations: entries,
  });
}

/* ------------------------------------------------------------------ *
 * Local Effect Runtime entrypoints                                   *
 * ------------------------------------------------------------------ */

async function local(value: unknown, kind: "inspect" | "prepare" | "confirm" | "history"): Promise<JsonObject> {
  let store: AuthorityStore | undefined;
  try {
    const record = canonicalAuthorityObject(value, "local goal closeout request");
    const root = requireAuthorityStoreId(record.runtime_root, "runtime_root");
    closeoutRequire(isAbsolute(root), "runtime_root must be absolute");
    const goalId = requireAuthorityStoreId(record.goal_id, "goal_id");
    const run = async () => {
      store = await openLocalAuthorityStore(root, goalId);
      if (kind === "inspect") return inspectGoalCloseout(store, goalId);
      if (kind === "history") return readGoalCloseoutHistory(store, goalId);
      return kind === "prepare"
        ? prepareGoalCloseout(store, canonicalAuthorityObject(value, "goal closeout request"))
        : confirmGoalCloseout(store, canonicalAuthorityObject(value, "goal closeout request"));
    };
    return kind === "inspect" || kind === "history"
      ? await run()
      : await withCanonicalWriter(root, goalId, record.dry_run === true, run);
  } catch (error) {
    const result = {...failure("closeout_invalid_request",
      error instanceof Error ? error.message : "goal closeout effect failed"),
      decision_read_from_provider: false, legacy_fallback_used: false, ...localAuthorityOpenFailure(error)};
    return store ? {...result, source_authority: authorityStoreSourceAuthority(store)} : result;
  }
}
export async function inspectLocalGoalCloseout(value: unknown): Promise<JsonObject> {
  return local(value, "inspect");
}
export async function prepareLocalGoalCloseout(value: unknown): Promise<JsonObject> {
  return local(value, "prepare");
}
export async function confirmLocalGoalCloseout(value: unknown): Promise<JsonObject> {
  return local(value, "confirm");
}
export async function readLocalGoalCloseoutHistory(value: unknown): Promise<JsonObject> {
  return local(value, "history");
}

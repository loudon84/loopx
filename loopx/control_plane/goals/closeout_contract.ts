/** Canonical Goal Closeout contract v1.
 *
 * TypeScript owns the Closeout state, the basis digests and the invariant
 * checks. Callers never supply a pass/fail result: `prepare` derives the
 * blockers from the canonical head, `confirm` re-derives them before it
 * commits. The object lives inside the Goal's existing authority head next to
 * `goal_acceptance`, under the same CAS commit that the acceptance authority
 * already uses, so no second store and no free-standing completion flag exist.
 *
 * `terminal_closeout` in the Turn settlement path is a *Turn*-level concept and
 * is deliberately unrelated to this Goal-level closeout.
 */
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {
  authorityUnicodeCompare,
  canonicalAuthorityObject,
  canonicalAuthoritySha256,
} from "../coordination/authority_store_codec.ts";
import {projectGoalAcceptance} from "./acceptance_contract.ts";

export const GOAL_CLOSEOUT_SCHEMA = "loopx_goal_closeout_v1";
export const GOAL_CLOSEOUT_POLICY = "strict_v1";
export const GOAL_CLOSEOUT_REQUEST_SCHEMA = "loopx_goal_closeout_request_v0";
export const GOAL_CLOSEOUT_RESULT_SCHEMA = "loopx_goal_closeout_result_v0";
export const GOAL_CLOSEOUT_RECEIPT_SCHEMA = "loopx_goal_closeout_operation_v0";
export const GOAL_CLOSEOUT_HISTORY_SCHEMA = "loopx_goal_closeout_history_v0";

/** Persisted states. `closeout_blocked` is a derived projection and is never
 * stored, so it cannot become a second write authority. */
export const GOAL_CLOSEOUT_STATES = [
  "active",
  "acceptance_pending",
  "acceptance_failed",
  "awaiting_owner_confirmation",
  "completed",
] as const;

/** Stable protocol rejection codes. Web/CLI/Host never parse English text. */
export const CLOSEOUT_REASON_CODES = [
  "closeout_open_required_todos",
  "closeout_unsettled_turn",
  "closeout_lease_conflict",
  "closeout_pending_gate",
  "closeout_monitor_not_retired",
  "closeout_acceptance_not_configured",
  "closeout_acceptance_failed",
  "closeout_acceptance_stale",
  "closeout_basis_stale",
  "closeout_owner_required",
  "closeout_owner_mismatch",
  "closeout_already_completed",
  "closeout_state_mismatch",
  "closeout_goal_ref_mismatch",
  "closeout_projection_sync_pending",
] as const;

export type CloseoutReasonCode = (typeof CLOSEOUT_REASON_CODES)[number];
export type GoalCloseoutState = (typeof GOAL_CLOSEOUT_STATES)[number];

export interface CloseoutBlocker {
  reason_code: CloseoutReasonCode;
  detail: string;
}

export interface GoalCloseoutBasis {
  provider_revision: string;
  required_todo_set_digest: string;
  todo_manifest_digest: string;
  acceptance_contract_digest: string | null;
  acceptance_verification_digest: string | null;
  outstanding_turns_digest: string;
  blocking_gates_digest: string;
  delivery_evidence_digest: string;
}

export interface GoalCloseoutStateRecord {
  schema_version: typeof GOAL_CLOSEOUT_SCHEMA;
  goal_ref: {goal_id: string; goal_instance_id: string} | null;
  policy: typeof GOAL_CLOSEOUT_POLICY;
  state: GoalCloseoutState;
  owner_principal_id: string | null;
  closeout_revision: number;
  basis: GoalCloseoutBasis;
  acceptance_receipt_id: string | null;
  owner_confirmation: JsonObject | null;
  completed_at: string | null;
  completion_receipt_id: string | null;
  closeout_operation_ids: string[];
}

const STATE_FIELDS = [
  "schema_version", "goal_ref", "policy", "state", "owner_principal_id",
  "closeout_revision", "basis", "acceptance_receipt_id", "owner_confirmation",
  "completed_at", "completion_receipt_id", "closeout_operation_ids",
];
const BASIS_FIELDS = [
  "provider_revision", "required_todo_set_digest", "todo_manifest_digest",
  "acceptance_contract_digest", "acceptance_verification_digest",
  "outstanding_turns_digest", "blocking_gates_digest", "delivery_evidence_digest",
];
const GOAL_REF_FIELDS = ["goal_id", "goal_instance_id"];
const CONFIRMATION_FIELDS = ["principal_id", "operation_id", "confirmed_at", "basis_digest"];

export function closeoutRequire(condition: unknown, message: string): asserts condition {
  if (!condition) throw new EffectRuntimeRequestError(message);
}

export function closeoutText(value: unknown, label: string, limit = 256): string {
  closeoutRequire(typeof value === "string" && value.trim().length > 0 && value.length <= limit,
    `${label} must be a bounded non-empty string`);
  return value as string;
}

function closeoutOptionalText(value: unknown, label: string, limit = 256): string | null {
  if (value === null || value === undefined) return null;
  return closeoutText(value, label, limit);
}

/** Exact key sets only: an unversioned field must never ride along silently. */
export function closeoutKeys(record: JsonObject, allowed: readonly string[], label: string): void {
  const allowedSet = new Set(allowed);
  const unexpected = Object.keys(record).filter((field) => !allowedSet.has(field))
    .sort(authorityUnicodeCompare);
  closeoutRequire(unexpected.length === 0,
    `${label} has unversioned fields: ${unexpected.join(", ")}`);
}

function closeoutBool(value: unknown, label: string): boolean {
  closeoutRequire(typeof value === "boolean", `${label} must be boolean`);
  return value as boolean;
}

/* ------------------------------------------------------------------ *
 * Head readers                                                       *
 * ------------------------------------------------------------------ */

function closeoutTodos(head: JsonObject): JsonObject[] {
  const todos = head.todos;
  if (todos === undefined) return [];
  closeoutRequire(Array.isArray(todos), "closeout requires a canonical Todo list");
  return (todos as JsonObject[]).map((todo) => canonicalAuthorityObject(todo, "canonical Todo"));
}

function isTerminalTodo(todo: JsonObject): boolean {
  if (todo.done === true) return true;
  const status = String(todo.status ?? "");
  return status === "done" || status === "superseded" || status === "retired";
}

function isUserGate(todo: JsonObject): boolean {
  return todo.role === "user" || todo.task_class === "user_gate";
}

function activeLeases(head: JsonObject): JsonObject[] {
  const leases = head.leases;
  if (leases === undefined) return [];
  closeoutRequire(Array.isArray(leases), "closeout requires a canonical lease list");
  return (leases as JsonObject[]).filter((lease) => {
    const record = canonicalAuthorityObject(lease, "canonical lease");
    return record.active === true || String(record.status ?? "") === "active";
  });
}

function pendingGates(head: JsonObject): JsonObject[] {
  const gates = head.blocking_gates;
  if (gates === undefined) return [];
  closeoutRequire(Array.isArray(gates), "blocking_gates must be an array");
  return (gates as JsonObject[]).filter((gate) => {
    const record = canonicalAuthorityObject(gate, "blocking gate");
    const state = String(record.state ?? record.status ?? "");
    return state !== "" && state !== "resolved" && state !== "closed";
  });
}

function unsettledTurns(head: JsonObject): JsonObject[] {
  const turns = head.unsettled_turns;
  if (turns === undefined) return [];
  closeoutRequire(Array.isArray(turns), "unsettled_turns must be an array");
  return (turns as JsonObject[]).map((turn) => canonicalAuthorityObject(turn, "unsettled Turn"));
}

export function goalCloseoutRefOf(goalId: string, goalInstanceId: unknown): JsonObject | null {
  if (typeof goalInstanceId !== "string" || goalInstanceId.length === 0) return null;
  return {goal_id: goalId, goal_instance_id: goalInstanceId};
}

/* ------------------------------------------------------------------ *
 * Evaluation                                                         *
 * ------------------------------------------------------------------ */

export interface GoalCloseoutEvaluation {
  state: GoalCloseoutState | "closeout_blocked";
  can_close: boolean;
  blockers: CloseoutBlocker[];
  basis: GoalCloseoutBasis;
  required_todo_ids: string[];
  satisfied_todo_ids: string[];
  acceptance_status: string;
  goal_id: string;
}

/** Every blocker is derived here, from the canonical head alone. */
export function evaluateGoalCloseout(
  head: JsonObject,
  goalId: string,
  providerRevision: string,
): GoalCloseoutEvaluation {
  closeoutRequire(head.goal_id === goalId, "goal closeout head identity mismatch");
  const blockers: CloseoutBlocker[] = [];
  const todos = closeoutTodos(head);
  const required: string[] = [];
  const satisfied: string[] = [];

  for (const todo of todos) {
    const todoId = String(todo.todo_id ?? "");
    if (!todoId) continue;
    if (isUserGate(todo)) {
      if (!isTerminalTodo(todo)) {
        blockers.push({reason_code: "closeout_pending_gate", detail: todoId});
      }
      continue;
    }
    if (todo.task_class === "continuous_monitor") {
      // A monitor never blocks delivery forever, but it must be explicitly
      // stopped or handed over — being non-advancement is not a waiver.
      if (isTerminalTodo(todo) || todo.monitor_retired === true) satisfied.push(todoId);
      else blockers.push({reason_code: "closeout_monitor_not_retired", detail: todoId});
      continue;
    }
    required.push(todoId);
    if (isTerminalTodo(todo)) satisfied.push(todoId);
    else blockers.push({reason_code: "closeout_open_required_todos", detail: todoId});
  }

  const openLeases = activeLeases(head);
  if (openLeases.length > 0) {
    blockers.push({reason_code: "closeout_lease_conflict",
      detail: openLeases.map((lease) => String(lease.todo_id ?? "unknown"))
        .sort(authorityUnicodeCompare).join(",")});
  }

  const gates = pendingGates(head);
  if (gates.length > 0) {
    blockers.push({reason_code: "closeout_pending_gate",
      detail: gates.map((gate) => String(gate.gate_id ?? gate.kind ?? "gate"))
        .sort(authorityUnicodeCompare).join(",")});
  }

  const turns = unsettledTurns(head);
  if (turns.length > 0) {
    blockers.push({reason_code: "closeout_unsettled_turn",
      detail: turns.map((turn) => String(turn.turn_instance_id ?? "turn"))
        .sort(authorityUnicodeCompare).join(",")});
  }

  const acceptance = projectGoalAcceptance(head, goalId);
  const acceptanceStatus = acceptance.enabled === true ? String(acceptance.status ?? "unverified") : "disabled";
  if (acceptance.enabled !== true) {
    blockers.push({reason_code: "closeout_acceptance_not_configured", detail: "goal acceptance is disabled or unbound"});
  } else if (acceptanceStatus === "stale" || acceptanceStatus === "held") {
    blockers.push({reason_code: "closeout_acceptance_stale", detail: acceptanceStatus});
  } else if (acceptanceStatus !== "accepted") {
    // unverified, failed and partial all mean "no real accepted receipt exists".
    blockers.push({reason_code: "closeout_acceptance_failed", detail: acceptanceStatus});
  }

  const basis: GoalCloseoutBasis = {
    provider_revision: providerRevision,
    required_todo_set_digest: canonicalAuthoritySha256(required.slice().sort(authorityUnicodeCompare)),
    todo_manifest_digest: canonicalAuthoritySha256(todos.map((todo) => ({
      todo_id: String(todo.todo_id ?? ""),
      status: String(todo.status ?? ""),
      done: todo.done === true,
      task_class: String(todo.task_class ?? ""),
      role: String(todo.role ?? ""),
    })).sort((left, right) => authorityUnicodeCompare(String(left.todo_id), String(right.todo_id)))),
    acceptance_contract_digest: acceptance.enabled === true ? String(acceptance.digest ?? "") || null : null,
    acceptance_verification_digest: canonicalAuthoritySha256(acceptance.verification ?? null),
    outstanding_turns_digest: canonicalAuthoritySha256(turns),
    blocking_gates_digest: canonicalAuthoritySha256(gates),
    delivery_evidence_digest: canonicalAuthoritySha256({
      required_todo_set_digest: canonicalAuthoritySha256(required.slice().sort(authorityUnicodeCompare)),
      acceptance_status: acceptanceStatus,
    }),
  };

  return {
    state: blockers.length === 0 ? "acceptance_pending" : "closeout_blocked",
    can_close: blockers.length === 0,
    blockers: blockers.sort((left, right) =>
      authorityUnicodeCompare(left.reason_code, right.reason_code) ||
      authorityUnicodeCompare(left.detail, right.detail)),
    basis,
    required_todo_ids: required.sort(authorityUnicodeCompare),
    satisfied_todo_ids: satisfied.sort(authorityUnicodeCompare),
    acceptance_status: acceptanceStatus,
    goal_id: goalId,
  };
}

export function closeoutBasisDigest(basis: GoalCloseoutBasis): string {
  return canonicalAuthoritySha256(basis as unknown as JsonObject);
}

/* ------------------------------------------------------------------ *
 * State codec                                                        *
 * ------------------------------------------------------------------ */

function normalizeBasis(value: unknown): GoalCloseoutBasis {
  const basis = canonicalAuthorityObject(value, "closeout basis");
  closeoutKeys(basis, BASIS_FIELDS, "closeout basis");
  const optional = (field: string): string | null => closeoutOptionalText(basis[field], `closeout basis ${field}`, 256);
  return {
    provider_revision: closeoutText(basis.provider_revision, "closeout basis provider_revision"),
    required_todo_set_digest: closeoutText(basis.required_todo_set_digest, "closeout basis required_todo_set_digest", 64),
    todo_manifest_digest: closeoutText(basis.todo_manifest_digest, "closeout basis todo_manifest_digest", 64),
    acceptance_contract_digest: optional("acceptance_contract_digest"),
    acceptance_verification_digest: optional("acceptance_verification_digest"),
    outstanding_turns_digest: closeoutText(basis.outstanding_turns_digest, "closeout basis outstanding_turns_digest", 64),
    blocking_gates_digest: closeoutText(basis.blocking_gates_digest, "closeout basis blocking_gates_digest", 64),
    delivery_evidence_digest: closeoutText(basis.delivery_evidence_digest, "closeout basis delivery_evidence_digest", 64),
  };
}

/** `completed` without its full evidence set is a protocol error, not a state. */
export function assertCloseoutInvariants(state: GoalCloseoutStateRecord): void {
  if (state.state !== "completed") return;
  closeoutRequire(state.goal_ref !== null, "completed Goal closeout requires goal_ref");
  closeoutRequire(state.owner_confirmation !== null, "completed Goal closeout requires owner_confirmation");
  closeoutRequire(state.acceptance_receipt_id !== null, "completed Goal closeout requires an acceptance receipt");
  closeoutRequire(state.completion_receipt_id !== null, "completed Goal closeout requires a completion receipt");
  closeoutRequire(state.completed_at !== null, "completed Goal closeout requires completed_at");
}

export function normalizeGoalCloseout(value: unknown): GoalCloseoutStateRecord {
  const record = canonicalAuthorityObject(value, "goal closeout");
  closeoutKeys(record, STATE_FIELDS, "goal closeout");
  closeoutRequire(record.schema_version === GOAL_CLOSEOUT_SCHEMA, "goal closeout schema mismatch");
  closeoutRequire(record.policy === GOAL_CLOSEOUT_POLICY, "goal closeout policy must be strict_v1");
  const state = String(record.state ?? "");
  closeoutRequire((GOAL_CLOSEOUT_STATES as readonly string[]).includes(state),
    "goal closeout state is invalid");
  closeoutRequire(Number.isSafeInteger(record.closeout_revision) && Number(record.closeout_revision) > 0,
    "goal closeout revision must be a positive safe integer");

  let goalRef: {goal_id: string; goal_instance_id: string} | null = null;
  if (record.goal_ref !== null && record.goal_ref !== undefined) {
    const ref = canonicalAuthorityObject(record.goal_ref, "closeout goal_ref");
    closeoutKeys(ref, GOAL_REF_FIELDS, "closeout goal_ref");
    goalRef = {
      goal_id: closeoutText(ref.goal_id, "closeout goal_ref goal_id"),
      goal_instance_id: closeoutText(ref.goal_instance_id, "closeout goal_ref goal_instance_id"),
    };
  }

  let confirmation: JsonObject | null = null;
  if (record.owner_confirmation !== null && record.owner_confirmation !== undefined) {
    const receipt = canonicalAuthorityObject(record.owner_confirmation, "closeout owner confirmation");
    closeoutKeys(receipt, CONFIRMATION_FIELDS, "closeout owner confirmation");
    confirmation = {
      principal_id: closeoutText(receipt.principal_id, "owner confirmation principal_id"),
      operation_id: closeoutText(receipt.operation_id, "owner confirmation operation_id"),
      confirmed_at: closeoutText(receipt.confirmed_at, "owner confirmation confirmed_at"),
      basis_digest: closeoutText(receipt.basis_digest, "owner confirmation basis_digest", 64),
    };
  }

  const operationIds = record.closeout_operation_ids;
  closeoutRequire(Array.isArray(operationIds), "goal closeout operation ids must be an array");
  closeoutRequire((operationIds as unknown[]).length <= 64, "goal closeout operation ids are unbounded");

  const normalized: GoalCloseoutStateRecord = {
    schema_version: GOAL_CLOSEOUT_SCHEMA,
    goal_ref: goalRef,
    policy: GOAL_CLOSEOUT_POLICY,
    state: state as GoalCloseoutState,
    owner_principal_id: closeoutOptionalText(record.owner_principal_id, "closeout owner_principal_id"),
    closeout_revision: Number(record.closeout_revision),
    basis: normalizeBasis(record.basis),
    acceptance_receipt_id: closeoutOptionalText(record.acceptance_receipt_id, "closeout acceptance_receipt_id"),
    owner_confirmation: confirmation,
    completed_at: closeoutOptionalText(record.completed_at, "closeout completed_at"),
    completion_receipt_id: closeoutOptionalText(record.completion_receipt_id, "closeout completion_receipt_id"),
    closeout_operation_ids: (operationIds as unknown[]).map((entry) =>
      closeoutText(entry, "closeout operation id")).slice(0, 64),
  };
  assertCloseoutInvariants(normalized);
  return normalized;
}

/** Read the Goal's own closeout record; another instance's record reads as absent. */
export function readGoalCloseout(
  head: JsonObject,
  goalId: string,
  goalRef?: {goal_id: string; goal_instance_id: string},
): GoalCloseoutStateRecord | null {
  if (!Object.hasOwn(head, "goal_closeout")) return null;
  const state = normalizeGoalCloseout(head.goal_closeout);
  if (state.goal_ref !== null) {
    closeoutRequire(state.goal_ref.goal_id === goalId, "goal closeout belongs to another Goal");
    if (goalRef !== undefined) {
      closeoutRequire(state.goal_ref.goal_id === goalRef.goal_id
        && state.goal_ref.goal_instance_id === goalRef.goal_instance_id,
      "goal closeout belongs to another Goal instance");
    }
  }
  return state;
}

export function initialGoalCloseoutState(
  goalId: string,
  goalRef: JsonObject | null,
  basis: GoalCloseoutBasis,
  ownerPrincipalId: string | null,
): GoalCloseoutStateRecord {
  return normalizeGoalCloseout({
    schema_version: GOAL_CLOSEOUT_SCHEMA,
    goal_ref: goalRef,
    policy: GOAL_CLOSEOUT_POLICY,
    state: "active",
    owner_principal_id: ownerPrincipalId,
    closeout_revision: 1,
    basis: basis as unknown as JsonObject,
    acceptance_receipt_id: null,
    owner_confirmation: null,
    completed_at: null,
    completion_receipt_id: null,
    closeout_operation_ids: [],
  });
}

/** Public projection. No validator stdout, credential or local path leaks out. */
export function projectGoalCloseout(head: JsonObject, goalId: string): JsonObject {
  const state = readGoalCloseout(head, goalId);
  if (state === null) {
    return {schema_version: GOAL_CLOSEOUT_SCHEMA, policy: GOAL_CLOSEOUT_POLICY,
      state: "active", present: false, completed: false};
  }
  return {
    schema_version: GOAL_CLOSEOUT_SCHEMA,
    policy: state.policy,
    present: true,
    state: state.state,
    completed: state.state === "completed",
    closeout_revision: state.closeout_revision,
    owner_confirmation_required: state.state === "awaiting_owner_confirmation",
    owner_principal_bound: state.owner_principal_id !== null,
    basis_digest: closeoutBasisDigest(state.basis),
    acceptance_receipt_id: state.acceptance_receipt_id,
    completed_at: state.completed_at,
    completion_receipt_id: state.completion_receipt_id,
    history_operation_count: state.closeout_operation_ids.length,
  };
}

/** `completed` is terminal: it is neither a stop nor a vision close. */
export function goalCloseoutIsCompleted(head: JsonObject, goalId: string): boolean {
  const state = readGoalCloseout(head, goalId);
  return state !== null && state.state === "completed";
}

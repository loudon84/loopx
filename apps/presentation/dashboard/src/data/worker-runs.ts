import { z } from "zod";

import { requestJson } from "./chat";

/**
 * Read-only governed-Turn run history for one Goal (FR-11 / AC-15).
 *
 * This is a projection of LoopX's recorded Turn journals, not evidence: it is
 * what lets a reader trace which Turn ran, when it was recorded, how it ended
 * and whether an independent validator passed. Nothing here is writable.
 */

const nullableText = z.string().nullable();

export const workerRunSchema = z.object({
  turn_label: z.string(),
  turn_instance_id: z.string(),
  todo_id: z.string(),
  // The journal records no wall-clock field, so this is the journal file's own
  // write time and `recorded_at_source` says so. Never read it as a Turn start.
  recorded_at: nullableText,
  recorded_at_source: nullableText,
  status: z.string(),
  result_kind: z.string(),
  completed_phases: z.array(z.string()),
  validation: z.object({
    status: z.string(),
    passed: z.boolean().nullable(),
    exit_code: z.number().int().nullable().optional(),
    validator_kind: nullableText.optional(),
    recovery_kind: nullableText.optional(),
  }),
  host_kind: z.string().optional(),
  runtime_kind: z.string().optional(),
  model: z.object({
    requested: nullableText.optional(),
    resolved: nullableText.optional(),
    applied: nullableText.optional(),
    observed: nullableText.optional(),
    source: nullableText.optional(),
    observed_unknown_reason: nullableText.optional(),
  }).optional(),
  route_source: nullableText.optional(),
  host_failure_kind: nullableText.optional(),
  host_attempt_count: z.number().int().nullable().optional(),
  recovery_from: nullableText.optional(),
  host_invoked: z.boolean().nullable().optional(),
  quota_spent: z.boolean().nullable().optional(),
  reason: nullableText.optional(),
});

export type WorkerRun = z.infer<typeof workerRunSchema>;

export const workerRunsSchema = z.object({
  ok: z.literal(true),
  schema_version: z.literal("loopx_worker_run_readback_v1"),
  goal_id: z.string(),
  turn_count: z.number().int().nonnegative(),
  returned: z.number().int().nonnegative(),
  runs: z.array(workerRunSchema),
});

export type WorkerRuns = z.infer<typeof workerRunsSchema>;

export async function fetchWorkerRuns(goalId: string, init?: RequestInit): Promise<WorkerRuns> {
  const query = new URLSearchParams({ goal_id: goalId });
  return workerRunsSchema.parse(
    await requestJson<unknown>(`/api/chat/worker-runs?${query.toString()}`, init),
  );
}

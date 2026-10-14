import type { WorkspaceTranslate } from "./i18n";
import type { WorkspaceActionPreviewRequest } from "./personal-workspace-model";

type GoalContent = { objective: string; completion: string; boundary: string };

/** The two executor routes the backend `goal.create` accepts. */
export type GoalExecutorRoute = "chat_agent" | "governed_turn_worker";

/** Worker endpoint id the backend Governed Turn Worker Catalog registers. */
export const DEFAULT_CODING_WORKER_ENDPOINT_ID = "dsh-coding-worker-local";

/** Builtin Coding Worker endpoints the create form may offer. */
export const CODING_WORKER_ENDPOINT_OPTIONS = [
  {
    id: "dsh-coding-worker-local",
    labelEn: "DSH Coding Worker",
    labelZh: "DSH Coding Worker",
  },
  {
    id: "codex-cli-coding-worker-local",
    labelEn: "Codex CLI Coding Worker",
    labelZh: "Codex CLI Coding Worker",
  },
  {
    id: "cursor-cli-coding-worker-local",
    labelEn: "Cursor CLI Coding Worker",
    labelZh: "Cursor CLI Coding Worker",
  },
] as const;

export function goalCreateContent(input: GoalContent, t: WorkspaceTranslate) {
  const objective = input.objective.trim();
  const completion = input.completion.trim();
  const boundary = input.boundary.trim();
  return {
    title: objective.slice(0, 80),
    objective: [objective, t("goal.objectiveCompletion", { criteria: completion }),
      boundary ? t("goal.objectiveBoundary", { boundary }) : ""].filter(Boolean).join("\n"),
    completion_criteria: completion, execution_boundary: boundary,
    initial_todos: [t("goal.initialTodo", { criteria: completion })],
  };
}

/** One preview builder for explicit forms and conversational drafts. No effects. */
export function goalCreateRequest(input: GoalContent & {
  agentId: string; permission: string; contextGoalId: string | null; operationId?: string;
  executorRoute?: GoalExecutorRoute; workerEndpointId?: string;
  workerModelDefault?: string; workerReasoningEffortDefault?: string;
}, t: WorkspaceTranslate): WorkspaceActionPreviewRequest {
  const content = goalCreateContent(input, t);
  // The backend routes are mutually exclusive: a Coding Worker Goal must not
  // carry a Chat agent endpoint id, and the default Chat route must keep
  // emitting exactly the historical payload.
  const executor = input.executorRoute === "governed_turn_worker"
    ? {
        executor_route: "governed_turn_worker" as const,
        worker_endpoint_id: input.workerEndpointId ?? DEFAULT_CODING_WORKER_ENDPOINT_ID,
        ...(input.workerModelDefault
          ? { worker_model_default: input.workerModelDefault }
          : {}),
        ...(input.workerReasoningEffortDefault
          ? { worker_reasoning_effort_default: input.workerReasoningEffortDefault }
          : {}),
      }
    : { agent_id: input.agentId };
  return {
    actionKind: "goal.create", context: { kind: "manager", goal_id: input.contextGoalId },
    idempotencyKey: `workspace-goal.create-${input.operationId ?? crypto.randomUUID()}`,
    summary: t("proposal.summary.goalCreate", { title: content.title }),
    normalizedParameters: {
      ...content, goal_id: `goal-${input.operationId ?? crypto.randomUUID()}`,
      ...executor, permission: input.permission,
      workspace_ref: "current", heartbeat: { enabled: false, cadence: "1d", timezone: "Asia/Shanghai" },
      stop_condition: "goal_complete",
    },
  };
}

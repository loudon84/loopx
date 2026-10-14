import { useEffect, useState } from "react";

import { fetchWorkerRuns, type WorkerRuns } from "../../data/worker-runs";
import { useWorkspaceI18n } from "./i18n";

/**
 * Governed-Turn run history for one Goal (FR-11 / AC-15).
 *
 * Read-only and lazily loaded: the route projects LoopX's recorded Turn
 * journals, so a reader can trace which Turn ran, when it was recorded, how it
 * ended, whether an independent validator passed, and which phases completed.
 *
 * Two honest labels are deliberate here:
 *  - the timestamp is the *journal write time*, not a Turn start/end time;
 *  - a missing validator verdict reads as unknown, never as passed.
 */

type WorkerRunsCopy = {
  title: string; readOnly: string; loading: string; error: string; empty: string;
  recorded: string; journalTime: string; validation: string; passed: string;
  failed: string; notRun: string; unknown: string; phases: string; failure: string;
  attempts: string; turn: string; truncated: string; runtime: string; model: string;
  routeSource: string;
};

const copy: Record<"en" | "zh", WorkerRunsCopy> = {
  en: {
    title: "Governed Turn runs",
    readOnly: "read-only",
    loading: "Loading run history…",
    error: "Could not read the run history.",
    empty: "No governed Turn has been recorded for this Goal yet.",
    recorded: "Recorded",
    journalTime: "journal write time",
    validation: "Validation",
    passed: "passed",
    failed: "failed",
    notRun: "not run",
    unknown: "unknown",
    phases: "phases",
    failure: "Failure",
    attempts: "attempts",
    turn: "Turn",
    truncated: "Showing the most recent runs only.",
    runtime: "Runtime",
    model: "Model",
    routeSource: "Route source",
  },
  zh: {
    title: "受控 Turn 运行记录",
    readOnly: "只读",
    loading: "正在读取运行记录…",
    error: "无法读取运行记录。",
    empty: "该 Goal 尚无已记录的受控 Turn。",
    recorded: "记录时间",
    journalTime: "journal 写入时间",
    validation: "独立验证",
    passed: "通过",
    failed: "未通过",
    notRun: "未运行",
    unknown: "未知",
    phases: "阶段",
    failure: "失败",
    attempts: "次尝试",
    turn: "Turn",
    truncated: "仅显示最近的运行记录。",
    runtime: "运行时",
    model: "模型",
    routeSource: "路由来源",
  },
} as const;

function validationLabel(run: WorkerRuns["runs"][number], labels: WorkerRunsCopy): string {
  const { passed, status } = run.validation;
  if (passed === true) return labels.passed;
  if (passed === false) return labels.failed;
  return status === "unknown" ? labels.unknown : labels.notRun;
}

export function WorkerRunsCard({ goalId }: { goalId: string }) {
  const { locale } = useWorkspaceI18n();
  const labels = locale === "zh-CN" ? copy.zh : copy.en;
  const [state, setState] = useState<{ phase: "loading" | "ready" | "error"; data: WorkerRuns | null }>({
    phase: "loading",
    data: null,
  });

  useEffect(() => {
    const controller = new AbortController();
    setState({ phase: "loading", data: null });
    void fetchWorkerRuns(goalId, { signal: controller.signal })
      .then((data) => setState({ phase: "ready", data }))
      .catch((failure: unknown) => {
        if (controller.signal.aborted) return;
        // A read failure is a read failure: never render it as "no runs".
        setState({ phase: "error", data: null });
        void failure;
      });
    return () => controller.abort();
  }, [goalId]);

  const runs = state.data?.runs ?? [];

  return (
    <section className="personal-detail-card personal-worker-runs" aria-label={labels.title}>
      <div className="personal-detail-card-title">
        <h3>{labels.title}</h3>
        <em>{labels.readOnly}</em>
      </div>
      {state.phase === "loading" ? <p role="status">{labels.loading}</p> : null}
      {state.phase === "error" ? <p role="alert">{labels.error}</p> : null}
      {state.phase === "ready" && runs.length === 0 ? <p role="status">{labels.empty}</p> : null}
      {runs.map((run) => (
        <div className="personal-worker-run" key={`${run.turn_label}:${run.turn_instance_id}`}>
          <p>
            <strong>{labels.turn}</strong> {run.turn_label || labels.unknown}
            {" · "}
            {run.recorded_at ?? labels.unknown}
            <small> ({run.recorded_at_source === "journal_file_mtime" ? labels.journalTime : labels.unknown})</small>
          </p>
          <dl>
            <div>
              <dt>{labels.validation}</dt>
              <dd>{validationLabel(run, labels)}</dd>
            </div>
            <div>
              <dt>{labels.phases}</dt>
              <dd>{run.completed_phases.length}</dd>
            </div>
            {run.runtime_kind ? (
              <div>
                <dt>{labels.runtime}</dt>
                <dd>{run.runtime_kind}</dd>
              </div>
            ) : null}
            {run.model?.applied || run.model?.resolved || run.model?.requested ? (
              <div>
                <dt>{labels.model}</dt>
                <dd>
                  {[
                    run.model.requested ? `req=${run.model.requested}` : null,
                    run.model.resolved ? `res=${run.model.resolved}` : null,
                    run.model.applied ? `app=${run.model.applied}` : null,
                    run.model.observed ? `obs=${run.model.observed}` : (run.model.observed_unknown_reason ? `obs=unknown(${run.model.observed_unknown_reason})` : null),
                  ].filter(Boolean).join(" · ")}
                </dd>
              </div>
            ) : null}
            {run.route_source ? (
              <div>
                <dt>{labels.routeSource}</dt>
                <dd>{run.route_source}</dd>
              </div>
            ) : null}
            {run.todo_id ? (
              <div>
                <dt>Todo</dt>
                <dd>{run.todo_id}</dd>
              </div>
            ) : null}
            {run.host_failure_kind ? (
              <div>
                <dt>{labels.failure}</dt>
                <dd>
                  {run.host_failure_kind}
                  {typeof run.host_attempt_count === "number"
                    ? ` · ${run.host_attempt_count} ${labels.attempts}`
                    : ""}
                </dd>
              </div>
            ) : null}
          </dl>
          {run.reason ? <p>{run.reason}</p> : null}
        </div>
      ))}
      {state.data && state.data.turn_count > state.data.returned ? (
        <p>{labels.truncated}</p>
      ) : null}
    </section>
  );
}

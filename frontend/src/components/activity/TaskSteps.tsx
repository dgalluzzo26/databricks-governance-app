/**
 * @file Status icon and step checklist for a tracked task.
 */
import type { TaskRecord, TaskStatus, TaskStep, TaskStepStatus } from "@/api/types";
import { CheckIcon, CrossIcon } from "@/components/icons";
import { formatDuration } from "./activityFormat";
import styles from "./TaskSteps.module.css";

const STATUS_LABEL: Record<TaskStatus | TaskStepStatus, string> = {
  running: "Running",
  succeeded: "Succeeded",
  done: "Done",
  failed: "Failed",
};

/**
 * Spinner, check, or cross for a task or step status.
 * @param props.status - Task or step status.
 */
export function StatusIcon({ status }: { status: TaskStatus | TaskStepStatus }) {
  const label = STATUS_LABEL[status];
  if (status === "running") {
    return <span className={styles.spinner} role="img" aria-label={label} />;
  }
  const failed = status === "failed";
  return (
    <span className={`${styles.icon} ${failed ? styles.failed : styles.done}`} role="img" aria-label={label}>
      {failed ? <CrossIcon width={12} height={12} /> : <CheckIcon width={12} height={12} />}
    </span>
  );
}

function stepSeconds(step: TaskStep): number | null {
  if (!step.finishedAt) return null;
  return (new Date(step.finishedAt).getTime() - new Date(step.startedAt).getTime()) / 1000;
}

/**
 * Ordered checklist of a task's steps; nested steps are indented.
 * @param props.task - Task whose steps to list.
 */
export function TaskStepList({ task }: { task: TaskRecord }) {
  if (task.steps.length === 0) {
    return <p className={styles.empty}>{task.status === "running" ? "Starting…" : "No steps recorded."}</p>;
  }
  return (
    <ol className={styles.steps} aria-label={`Steps for ${task.title}`}>
      {task.steps.map((step, index) => (
        <li
          key={`${step.startedAt}-${index}`}
          className={`${styles.step}${step.status === "failed" ? ` ${styles.stepFailed}` : ""}`}
          style={{ paddingLeft: `${Math.min(step.depth, 4) * 16}px` }}
        >
          <StatusIcon status={step.status} />
          <span className={styles.stepBody}>
            <span className={styles.stepName}>{step.name}</span>
            {step.detail ? <span className={styles.stepDetail}>{step.detail}</span> : null}
          </span>
          {step.status !== "running" ? <span className={styles.stepTime}>{formatDuration(stepSeconds(step))}</span> : null}
        </li>
      ))}
    </ol>
  );
}

/**
 * Counts finished steps, for example `3 of 5 steps`.
 * @param task - Task to summarize.
 */
export function stepProgress(task: TaskRecord): string {
  const done = task.steps.filter((step) => step.status !== "running").length;
  return `${done} of ${task.steps.length} step${task.steps.length === 1 ? "" : "s"}`;
}

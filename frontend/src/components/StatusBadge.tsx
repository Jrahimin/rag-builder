type StatusTone = "healthy" | "warning" | "danger" | "info" | "neutral";

const healthyStates = new Set([
  "ready",
  "ok",
  "healthy",
  "active",
  "success",
  "succeeded",
  "completed",
  "embedded",
  "chunked",
  "locked",
  "passed",
  "grounded",
]);
const warningStates = new Set([
  "degraded",
  "retry_scheduled",
  "stale",
  "skipped",
  "queued",
  "parsing",
  "chunking",
  "embedding",
  "indexing",
  "deferred",
  "migration required",
  "needs_attention",
  "deleting",
  "purging",
]);
const dangerStates = new Set(["down", "failed", "failure", "unavailable", "offline"]);

function statusTone(status: string): StatusTone {
  if (healthyStates.has(status)) return "healthy";
  if (warningStates.has(status)) return "warning";
  if (dangerStates.has(status)) return "danger";
  if (status === "running" || status === "uploaded" || status === "in_progress") return "info";
  return "neutral";
}

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  return (
    <span className={`status-badge status-badge--${statusTone(status)}`}>
      <span className="status-badge__dot" aria-hidden="true" />
      {label ?? status.replaceAll("_", " ")}
    </span>
  );
}

type StatusTone = "healthy" | "warning" | "danger" | "info" | "neutral";

const healthyStates = new Set([
  "ready",
  "ok",
  "healthy",
  "active",
  "succeeded",
  "completed",
  "embedded",
  "chunked",
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
]);
const dangerStates = new Set(["down", "failed", "unavailable", "offline"]);

function statusTone(status: string): StatusTone {
  if (healthyStates.has(status)) return "healthy";
  if (warningStates.has(status)) return "warning";
  if (dangerStates.has(status)) return "danger";
  if (status === "running" || status === "uploaded") return "info";
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

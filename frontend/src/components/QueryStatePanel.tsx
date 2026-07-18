import { AlertTriangle, Inbox, RefreshCw, ServerOff } from "lucide-react";
import { OperatorApiError } from "../api/operatorApiClient";

export function LoadingState({ label = "Loading operational data" }: { label?: string }) {
  return (
    <div className="query-state" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <p>{label}…</p>
    </div>
  );
}

export function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="query-state">
      <Inbox aria-hidden="true" />
      <h2>{title}</h2>
      <p>{detail}</p>
    </div>
  );
}

export function ErrorState({ error, retry }: { error: Error; retry: () => void }) {
  const unavailable = error instanceof OperatorApiError && error.code === "backend_unavailable";
  const Icon = unavailable ? ServerOff : AlertTriangle;
  return (
    <div className="query-state query-state--error" role="alert">
      <Icon aria-hidden="true" />
      <h2>{unavailable ? "Backend unavailable" : "Operational data failed to load"}</h2>
      <p>{error.message}</p>
      <button className="button button--secondary" type="button" onClick={retry}>
        <RefreshCw size={16} aria-hidden="true" /> Try again
      </button>
    </div>
  );
}

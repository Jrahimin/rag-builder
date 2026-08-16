import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { OperatorNavigation } from "./OperatorNavigation";
import { useAdminAuth } from "../auth/useAdminAuth";

const titles: Record<string, { title: string; description: string }> = {
  "/": { title: "Overview", description: "Deployment status and key operational activity" },
  "/jobs": {
    title: "Jobs",
    description: "Monitor durable runs, inspect details, and retry safe failures",
  },
  "/lab": {
    title: "Test Lab",
    description: "Browser-based end-to-end product verification",
  },
  "/projects": {
    title: "Projects",
    description: "Canonical ownership, AI policy, documents, and source administration",
  },
  "/organizations": {
    title: "Organizations",
    description: "Client records, credentials, lifecycle, and associated Projects",
  },
  "/configuration": {
    title: "Configuration",
    description: "Read-only active runtime and index configuration",
  },
  "/metrics": { title: "Metrics", description: "Queue, latency, usage, and corpus measurements" },
  "/quality": {
    title: "Evidence Quality",
    description: "Reproducible retrieval, groundedness, refusal, and reranker decisions",
  },
  "/audit": { title: "Audit", description: "Recent deployment and durable-job activity" },
  "/webhooks": {
    title: "Webhooks",
    description: "Signed integration endpoints, delivery attempts, failures, and replay",
  },
  "/health": {
    title: "System Health",
    description: "Dependencies, startup checks, and worker heartbeats",
  },
};

export function OperatorConsoleLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const auth = useAdminAuth();
  const heading = titles[location.pathname] ?? titles["/"]!;
  return (
    <div className="console-shell">
      <OperatorNavigation />
      <main className="console-main" id="main-content">
        <header className="page-header">
          <div>
            <p className="eyebrow">Operator console</p>
            <h1>{heading.title}</h1>
            <p>{heading.description}</p>
          </div>
          <div className="live-indicator">
            <span aria-hidden="true" /> Live data
          </div>
          <div className="admin-account">
            <span>{auth.admin?.email}</span>
            <small>Super Admin</small>
            <button
              type="button"
              onClick={() => {
                void auth.logout().then(
                  () => void navigate("/login", { replace: true }),
                  () => void navigate("/login", { replace: true }),
                );
              }}
            >
              Log out
            </button>
          </div>
        </header>
        <Outlet />
      </main>
    </div>
  );
}

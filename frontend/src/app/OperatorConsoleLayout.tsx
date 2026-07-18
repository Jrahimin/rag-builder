import { Outlet, useLocation } from "react-router-dom";
import { OperatorNavigation } from "./OperatorNavigation";

const titles: Record<string, { title: string; description: string }> = {
  "/": { title: "Overview", description: "Deployment status and key operational activity" },
  "/jobs": {
    title: "Jobs",
    description: "Monitor durable runs, inspect details, and retry safe failures",
  },
  "/projects": {
    title: "Projects / Documents",
    description: "Inspect corpus scope and document processing lifecycle",
  },
  "/configuration": {
    title: "Configuration",
    description: "Read-only active runtime and index configuration",
  },
  "/metrics": { title: "Metrics", description: "Queue, latency, usage, and corpus measurements" },
  "/audit": { title: "Audit", description: "Recent deployment and durable-job activity" },
  "/health": {
    title: "System Health",
    description: "Dependencies, startup checks, and worker heartbeats",
  },
};

export function OperatorConsoleLayout() {
  const location = useLocation();
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
        </header>
        <Outlet />
      </main>
    </div>
  );
}

import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { LoadingState } from "../components/QueryStatePanel";
import { OperatorConsoleLayout } from "./OperatorConsoleLayout";

const AuditHistory = lazy(() =>
  import("../features/audit/AuditHistory").then((module) => ({ default: module.AuditHistory })),
);
const ActiveConfigurationDetails = lazy(() =>
  import("../features/configuration/ActiveConfigurationDetails").then((module) => ({
    default: module.ActiveConfigurationDetails,
  })),
);
const JobRuns = lazy(() =>
  import("../features/jobs/JobRuns").then((module) => ({ default: module.JobRuns })),
);
const TestLab = lazy(() =>
  import("../features/lab/TestLab").then((module) => ({ default: module.TestLab })),
);
const OperationalMetrics = lazy(() =>
  import("../features/metrics/OperationalMetrics").then((module) => ({
    default: module.OperationalMetrics,
  })),
);
const SystemHealthOverview = lazy(() =>
  import("../features/overview/SystemHealthOverview").then((module) => ({
    default: module.SystemHealthOverview,
  })),
);
const EvidenceQuality = lazy(() =>
  import("../features/quality/EvidenceQuality").then((module) => ({
    default: module.EvidenceQuality,
  })),
);
const ProjectDocumentInspection = lazy(() =>
  import("../features/projects/ProjectDocumentInspection").then((module) => ({
    default: module.ProjectDocumentInspection,
  })),
);
const DependencyWorkerHealth = lazy(() =>
  import("../features/system-health/DependencyWorkerHealth").then((module) => ({
    default: module.DependencyWorkerHealth,
  })),
);
const WebhookDeliveryInspection = lazy(() =>
  import("../features/webhooks/WebhookDeliveryInspection").then((module) => ({
    default: module.WebhookDeliveryInspection,
  })),
);

export function OperatorConsoleApp() {
  return (
    <Routes>
      <Route element={<OperatorConsoleLayout />}>
        <Route
          index
          element={
            <Suspense fallback={<LoadingState label="Loading overview" />}>
              <SystemHealthOverview />
            </Suspense>
          }
        />
        <Route
          path="lab"
          element={
            <Suspense fallback={<LoadingState label="Loading Test Lab" />}>
              <TestLab />
            </Suspense>
          }
        />
        <Route
          path="jobs"
          element={
            <Suspense fallback={<LoadingState label="Loading jobs" />}>
              <JobRuns />
            </Suspense>
          }
        />
        <Route
          path="projects"
          element={
            <Suspense fallback={<LoadingState label="Loading projects" />}>
              <ProjectDocumentInspection />
            </Suspense>
          }
        />
        <Route
          path="configuration"
          element={
            <Suspense fallback={<LoadingState label="Loading configuration" />}>
              <ActiveConfigurationDetails />
            </Suspense>
          }
        />
        <Route
          path="metrics"
          element={
            <Suspense fallback={<LoadingState label="Loading metrics" />}>
              <OperationalMetrics />
            </Suspense>
          }
        />
        <Route
          path="quality"
          element={
            <Suspense fallback={<LoadingState label="Loading evidence quality" />}>
              <EvidenceQuality />
            </Suspense>
          }
        />
        <Route
          path="audit"
          element={
            <Suspense fallback={<LoadingState label="Loading audit" />}>
              <AuditHistory />
            </Suspense>
          }
        />
        <Route
          path="webhooks"
          element={
            <Suspense fallback={<LoadingState label="Loading webhooks" />}>
              <WebhookDeliveryInspection />
            </Suspense>
          }
        />
        <Route
          path="health"
          element={
            <Suspense fallback={<LoadingState label="Loading system health" />}>
              <DependencyWorkerHealth />
            </Suspense>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

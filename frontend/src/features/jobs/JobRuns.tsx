import { useMemo, useState } from "react";
import { useAllJobs, useJobs, useProjects } from "../../api/operatorConsoleQueries";
import { EmptyState, ErrorState, LoadingState } from "../../components/QueryStatePanel";
import { ProjectSelector } from "../../components/ProjectSelector";
import { StatusBadge } from "../../components/StatusBadge";
import { formatDate, shortId } from "../../shared/formatters";
import { JobRunDetails } from "./JobRunDetails";

export function JobRuns() {
  const projects = useProjects();
  const [projectId, setProjectId] = useState("");
  const [state, setState] = useState("");
  const [jobType, setJobType] = useState("");
  const [selectedJob, setSelectedJob] = useState<{ id: string; projectId: string } | null>(null);
  const allJobs = useAllJobs(projects.data?.items ?? [], state, jobType, !projectId);
  const projectJobs = useJobs(projectId, state, jobType);
  const jobs = projectId ? projectJobs : allJobs;
  const items = useMemo(() => jobs.data?.items ?? [], [jobs.data]);

  if (projects.isPending) return <LoadingState label="Loading projects" />;
  if (projects.isError)
    return <ErrorState error={projects.error} retry={() => void projects.refetch()} />;
  if (!projects.data.items.length)
    return (
      <EmptyState
        title="No projects yet"
        detail="Create a project through the API before monitoring durable jobs."
      />
    );

  return (
    <div className={`workspace-grid ${selectedJob ? "workspace-grid--detail" : ""}`}>
      <section className="panel workspace-grid__main">
        <div className="toolbar">
          <ProjectSelector
            projects={projects.data.items}
            value={projectId}
            includeAll
            onChange={(value) => {
              setProjectId(value);
              setSelectedJob(null);
            }}
          />
          <label className="field-control">
            <span>Status</span>
            <select value={state} onChange={(event) => setState(event.target.value)}>
              <option value="">All states</option>
              <option value="queued">Queued</option>
              <option value="running">Running</option>
              <option value="retry_scheduled">Retry scheduled</option>
              <option value="succeeded">Succeeded</option>
              <option value="failed">Failed</option>
            </select>
          </label>
          <label className="field-control">
            <span>Type</span>
            <select value={jobType} onChange={(event) => setJobType(event.target.value)}>
              <option value="">All types</option>
              <option value="document.process">Process</option>
              <option value="document.embed">Embed</option>
              <option value="document.index">Index</option>
            </select>
          </label>
          <span className="polling-note">Auto-refreshes active jobs every 3s</span>
        </div>
        {jobs.isPending ? (
          <LoadingState label="Loading jobs" />
        ) : jobs.isError ? (
          <ErrorState error={jobs.error} retry={() => void jobs.refetch()} />
        ) : items.length === 0 ? (
          <EmptyState
            title="No matching jobs"
            detail="No durable runs match the current project and filters."
          />
        ) : (
          <div className="table-scroll">
            <table>
              <caption className="sr-only">Durable job runs</caption>
              <thead>
                <tr>
                  <th>Job run</th>
                  <th>Type</th>
                  <th>Status</th>
                  <th>Stage</th>
                  <th>Progress</th>
                  <th>Attempts</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {items.map((job) => (
                  <tr key={job.id} className={selectedJob?.id === job.id ? "row--selected" : ""}>
                    <td>
                      <button
                        className="table-link"
                        type="button"
                        onClick={() => setSelectedJob({ id: job.id, projectId: job.project_id })}
                        aria-label={`Inspect job ${job.id}`}
                      >
                        {shortId(job.id)}
                      </button>
                    </td>
                    <td>{job.job_type.replace("document.", "")}</td>
                    <td>
                      <StatusBadge status={job.state} />
                    </td>
                    <td>{job.stage}</td>
                    <td>
                      <div className="table-progress">
                        <span style={{ width: `${job.progress}%` }} />
                      </div>
                      <small>{job.progress}%</small>
                    </td>
                    <td>
                      {job.attempt_count} / {job.max_attempts}
                    </td>
                    <td>{formatDate(job.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {selectedJob && (
        <JobRunDetails
          projectId={selectedJob.projectId}
          jobId={selectedJob.id}
          onClose={() => setSelectedJob(null)}
        />
      )}
    </div>
  );
}

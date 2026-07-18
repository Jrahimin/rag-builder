import { AlertTriangle, CheckCircle2, Play, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  useCreateEvaluationRun,
  useEvaluationDatasets,
  useProjects,
  useQuality,
} from "../../api/operatorConsoleQueries";
import { MetricCard } from "../../components/MetricCard";
import { ProjectSelector } from "../../components/ProjectSelector";
import { EmptyState, ErrorState, LoadingState } from "../../components/QueryStatePanel";
import { StatusBadge } from "../../components/StatusBadge";

type JsonRecord = Record<string, unknown>;

function record(value: unknown): JsonRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as JsonRecord) : {};
}

function records(value: unknown): JsonRecord[] {
  return Array.isArray(value) ? value.map(record) : [];
}

function percent(value: unknown): string {
  return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "—";
}

function milliseconds(value: unknown): string {
  return typeof value === "number" ? `${value.toFixed(0)} ms` : "—";
}

function scalar(value: unknown, fallback = "—"): string {
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean"
    ? String(value)
    : fallback;
}

export function EvidenceQuality() {
  const projects = useProjects();
  const [projectId, setProjectId] = useState("");
  const [datasetId, setDatasetId] = useState("");
  const quality = useQuality(projectId);
  const datasets = useEvaluationDatasets(projectId);
  const createRun = useCreateEvaluationRun(projectId);

  useEffect(() => {
    if (!projectId && projects.data?.items[0]) setProjectId(projects.data.items[0].id);
  }, [projectId, projects.data]);

  useEffect(() => {
    const latest = datasets.data?.[0];
    if (latest && !datasets.data?.some((dataset) => dataset.id === datasetId)) {
      setDatasetId(latest.id);
    }
  }, [datasetId, datasets.data]);

  const activeMetrics = useMemo(() => {
    const run = quality.data?.last_run;
    if (!run) return {};
    const comparison = record(run.reranker_comparison);
    const profile =
      typeof comparison.active_profile === "string" ? comparison.active_profile : "hybrid";
    return record(record(run.metrics)[profile]);
  }, [quality.data]);

  if (projects.isPending) return <LoadingState label="Loading projects" />;
  if (projects.isError)
    return <ErrorState error={projects.error} retry={() => void projects.refetch()} />;
  if (!projects.data.items.length)
    return (
      <EmptyState title="No projects" detail="Create a Project before running quality checks." />
    );
  if (!projectId) return <LoadingState label="Selecting project" />;
  if (quality.isPending || datasets.isPending) return <LoadingState label="Loading quality data" />;
  if (quality.isError)
    return <ErrorState error={quality.error} retry={() => void quality.refetch()} />;
  if (datasets.isError)
    return <ErrorState error={datasets.error} retry={() => void datasets.refetch()} />;

  const summary = quality.data;
  const run = summary.last_run;
  const comparison = record(run?.reranker_comparison);
  const candidates = records(comparison.candidates);

  return (
    <div className="page-stack">
      <section className="panel">
        <div className="toolbar">
          <ProjectSelector
            projects={projects.data.items}
            value={projectId}
            onChange={setProjectId}
          />
          <label className="field-control field-control--grow">
            <span>Dataset version</span>
            <select value={datasetId} onChange={(event) => setDatasetId(event.target.value)}>
              {datasets.data.map((dataset) => (
                <option key={dataset.id} value={dataset.id}>
                  {dataset.name} · {dataset.version}
                </option>
              ))}
            </select>
          </label>
          <button
            className="button button--primary"
            type="button"
            disabled={!datasetId || createRun.isPending}
            onClick={() => createRun.mutate(datasetId)}
          >
            <Play size={16} aria-hidden="true" />{" "}
            {createRun.isPending ? "Queueing…" : "Run quality"}
          </button>
        </div>
        {!datasets.data.length && (
          <div className="inline-empty">
            <AlertTriangle size={18} aria-hidden="true" /> Create a versioned dataset through the
            evaluation API before running quality checks.
          </div>
        )}
      </section>

      {createRun.isError && <p className="error-note">{createRun.error.message}</p>}
      {createRun.isSuccess && (
        <p className="success-note">Quality run queued as job {createRun.data.job_id}.</p>
      )}

      {!run ? (
        <EmptyState
          title="No quality runs yet"
          detail="Choose an immutable dataset version and queue the first reproducible run."
        />
      ) : (
        <>
          <section className="metric-grid">
            <MetricCard
              label="Recall@k"
              value={percent(activeMetrics.recall_at_k)}
              detail={`Active profile · top ${run.top_k}`}
              icon={ShieldCheck}
            />
            <MetricCard
              label="nDCG"
              value={percent(activeMetrics.ndcg)}
              detail={`MRR ${percent(activeMetrics.mrr)}`}
              icon={CheckCircle2}
            />
            <MetricCard
              label="Groundedness"
              value={percent(activeMetrics.groundedness)}
              detail={`Citation coverage ${percent(activeMetrics.citation_coverage)}`}
              icon={ShieldCheck}
            />
            <MetricCard
              label="Retrieval p95"
              value={milliseconds(activeMetrics.latency_p95_ms)}
              detail={`Refusal accuracy ${percent(activeMetrics.refusal_accuracy)}`}
              icon={CheckCircle2}
            />
          </section>

          <section className="panel">
            <div className="panel__heading">
              <div>
                <h2>Reproducibility</h2>
                <p>Immutable dataset and exact configuration captured for the latest run</p>
              </div>
              <StatusBadge status={run.job_state} />
            </div>
            <dl className="configuration-grid">
              <div>
                <dt>Dataset</dt>
                <dd>
                  {summary.dataset ? `${summary.dataset.name} ${summary.dataset.version}` : "—"}
                </dd>
              </div>
              <div>
                <dt>Dataset hash</dt>
                <dd>
                  <code>{summary.dataset?.dataset_hash.slice(0, 12) ?? "—"}</code>
                </dd>
              </div>
              <div>
                <dt>Configuration hash</dt>
                <dd>
                  <code>{run.configuration_hash.slice(0, 12)}</code>
                </dd>
              </div>
              <div>
                <dt>Prompt version</dt>
                <dd>{scalar(record(run.versions).prompt_version)}</dd>
              </div>
              <div>
                <dt>Embedding set</dt>
                <dd>{scalar(record(record(run.versions).retrieval).embedding_set_version)}</dd>
              </div>
              <div>
                <dt>Corpus fingerprint</dt>
                <dd>
                  <code>
                    {scalar(record(record(run.versions).corpus).fingerprint).slice(0, 12)}
                  </code>
                </dd>
              </div>
              <div>
                <dt>Failed cases</dt>
                <dd>{run.failed_cases.length}</dd>
              </div>
            </dl>
          </section>

          <div className="split-grid">
            <section className="panel">
              <div className="panel__heading">
                <div>
                  <h2>Reranker comparison</h2>
                  <p>{scalar(comparison.promotion_reason, "No comparison completed")}</p>
                </div>
              </div>
              {candidates.length ? (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Profile</th>
                        <th>nDCG gain</th>
                        <th>Groundedness</th>
                        <th>p95 penalty</th>
                        <th>Decision</th>
                      </tr>
                    </thead>
                    <tbody>
                      {candidates.map((candidate) => (
                        <tr key={String(candidate.profile)}>
                          <td>{String(candidate.profile)}</td>
                          <td>{percent(candidate.ndcg_gain)}</td>
                          <td>{percent(candidate.groundedness_gain)}</td>
                          <td>{milliseconds(candidate.p95_latency_penalty_ms)}</td>
                          <td>
                            <StatusBadge
                              status={
                                candidate.eligible_for_promotion ? "eligible" : "not eligible"
                              }
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="inline-empty">Comparison is available after the run completes.</div>
              )}
            </section>

            <section className="panel">
              <div className="panel__heading">
                <div>
                  <h2>Regressions and failures</h2>
                  <p>Cases requiring investigation</p>
                </div>
              </div>
              {!run.regressions.length && !run.failed_cases.length ? (
                <div className="inline-empty">
                  <CheckCircle2 size={18} aria-hidden="true" /> No regressions or failed cases.
                </div>
              ) : (
                <ul className="event-list">
                  {records(run.regressions).map((item, index) => (
                    <li key={`regression-${index}`}>
                      <AlertTriangle size={18} />
                      <div>
                        <strong>{String(item.metric)}</strong>
                        <span>
                          {String(item.previous)} → {String(item.current)}
                        </span>
                      </div>
                    </li>
                  ))}
                  {records(run.failed_cases).map((item) => (
                    <li key={String(item.case_key)}>
                      <AlertTriangle size={18} />
                      <div>
                        <strong>{String(item.case_key)}</strong>
                        <span>
                          {Array.isArray(item.reasons) ? item.reasons.join(", ") : "Failed"}
                        </span>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        </>
      )}
    </div>
  );
}

import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Eye, KeyRound, Settings2 } from "lucide-react";
import { operatorApiClient } from "../../api/operatorApiClient";
import { operatorQueryKeys } from "../../api/operatorConsoleQueries";
import { ErrorState, LoadingState } from "../../components/QueryStatePanel";
import { StatusBadge } from "../../components/StatusBadge";
import { formatDate, shortId } from "../../shared/formatters";

export function ActiveConfigurationDetails() {
  const configuration = useQuery({
    queryKey: operatorQueryKeys.configuration,
    queryFn: operatorApiClient.getConfiguration,
    staleTime: 60_000,
  });
  if (configuration.isPending) return <LoadingState label="Loading active configuration" />;
  if (configuration.isError)
    return <ErrorState error={configuration.error} retry={() => void configuration.refetch()} />;
  const config = configuration.data;
  const rows = [
    ["Runtime profile", config.runtime_profile],
    ["Environment", config.environment],
    ["Application version", config.application_version],
    ["Storage", config.storage_backend],
    ["Job backend", config.job_backend],
    ["Retrieval strategy", config.retrieval_strategy],
    ["Reranker", config.reranker_backend],
    ["OCR", config.ocr_enabled ? config.ocr_backend : "disabled"],
    ["Index version", `v${config.embedding_set_version}`],
  ];
  return (
    <div className="page-stack">
      <section className="notice-card">
        <Eye aria-hidden="true" />
        <div>
          <strong>Read-only configuration</strong>
          <p>
            This view is sanitized by the backend. Provider credentials and secret values never
            enter the browser.
          </p>
        </div>
      </section>
      <div className="provider-grid">
        <section className="panel provider-card">
          <div className="provider-card__title">
            <Settings2 aria-hidden="true" />
            <div>
              <h2>Language model</h2>
              <p>Active generation provider</p>
            </div>
            <StatusBadge status="active" />
          </div>
          <dl className="detail-list">
            <div>
              <dt>Backend</dt>
              <dd>{config.llm.backend}</dd>
            </div>
            <div>
              <dt>Model</dt>
              <dd>{config.llm.model ?? "—"}</dd>
            </div>
            <div>
              <dt>Provider version</dt>
              <dd>{config.llm.provider_version ?? "—"}</dd>
            </div>
            <div>
              <dt>Credential</dt>
              <dd>
                {config.llm.credential_configured === null ||
                config.llm.credential_configured === undefined
                  ? "Not applicable"
                  : config.llm.credential_configured
                    ? "Configured"
                    : "Missing"}
              </dd>
            </div>
          </dl>
        </section>
        <section className="panel provider-card">
          <div className="provider-card__title">
            <KeyRound aria-hidden="true" />
            <div>
              <h2>Embedding model</h2>
              <p>Active vector generation provider</p>
            </div>
            <StatusBadge status="active" />
          </div>
          <dl className="detail-list">
            <div>
              <dt>Backend</dt>
              <dd>{config.embedding.backend}</dd>
            </div>
            <div>
              <dt>Model</dt>
              <dd>{config.embedding.model ?? "—"}</dd>
            </div>
            <div>
              <dt>Dimensions</dt>
              <dd>{config.embedding.dimensions ?? "—"}</dd>
            </div>
            <div>
              <dt>Credential</dt>
              <dd>
                {config.embedding.credential_configured === null ||
                config.embedding.credential_configured === undefined
                  ? "Not applicable"
                  : config.embedding.credential_configured
                    ? "Configured"
                    : "Missing"}
              </dd>
            </div>
          </dl>
        </section>
      </div>
      <section className="panel">
        <div className="panel__heading">
          <div>
            <h2>Runtime configuration</h2>
            <p>Effective deployment-level choices</p>
          </div>
        </div>
        <dl className="configuration-grid">
          {rows.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      </section>
      <section className="panel">
        <div className="panel__heading">
          <div>
            <h2>Recent project snapshots</h2>
            <p>Immutable, secret-free configuration fingerprints</p>
          </div>
        </div>
        {config.recent_project_snapshots.length === 0 ? (
          <div className="inline-empty">
            <CheckCircle2 size={18} aria-hidden="true" /> No job configuration snapshots yet.
          </div>
        ) : (
          <div className="table-scroll">
            <table>
              <caption className="sr-only">Recent project configuration snapshots</caption>
              <thead>
                <tr>
                  <th>Project</th>
                  <th>Snapshot</th>
                  <th>Schema</th>
                  <th>Hash</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {config.recent_project_snapshots.map((snapshot) => (
                  <tr key={snapshot.snapshot_id}>
                    <td>{shortId(snapshot.project_id)}</td>
                    <td>{shortId(snapshot.snapshot_id)}</td>
                    <td>v{snapshot.schema_version}</td>
                    <td>
                      <code>{snapshot.configuration_hash.slice(0, 16)}…</code>
                    </td>
                    <td>{formatDate(snapshot.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

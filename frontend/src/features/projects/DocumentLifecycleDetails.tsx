import { Check, Circle, X } from "lucide-react";
import type { Document } from "../../api/operatorApiClient";
import { StatusBadge } from "../../components/StatusBadge";
import { formatBytes, formatDate } from "../../shared/formatters";

const stages = ["uploaded", "parsing", "chunking", "embedding", "indexing", "ready"];
const stageIndex: Record<string, number> = {
  uploaded: 0,
  queued: 0,
  parsing: 1,
  chunking: 2,
  chunked: 2,
  embedding: 3,
  embedded: 3,
  indexing: 4,
  ready: 5,
  failed: -1,
};

export function DocumentLifecycleDetails({
  document,
  onClose,
}: {
  document: Document;
  onClose: () => void;
}) {
  const activeIndex = stageIndex[document.status] ?? 0;
  return (
    <aside className="detail-panel" aria-label="Document details">
      <div className="detail-panel__header">
        <div>
          <p className="eyebrow">Document</p>
          <h2>{document.filename}</h2>
        </div>
        <button
          className="icon-button"
          type="button"
          onClick={onClose}
          aria-label="Close document details"
        >
          <X aria-hidden="true" />
        </button>
      </div>
      <div className="detail-panel__body">
        <StatusBadge status={document.status} />
        <dl className="detail-list">
          <div>
            <dt>Size</dt>
            <dd>{formatBytes(document.size_bytes)}</dd>
          </div>
          <div>
            <dt>Version</dt>
            <dd>{document.version}</dd>
          </div>
          <div>
            <dt>Updated</dt>
            <dd>{formatDate(document.updated_at)}</dd>
          </div>
          <div>
            <dt>Parser</dt>
            <dd>{document.accepted_parser ?? document.parser_name ?? "—"}</dd>
          </div>
          <div>
            <dt>Parse quality</dt>
            <dd>
              {document.parse_quality_score === null || document.parse_quality_score === undefined
                ? "—"
                : `${Math.round(document.parse_quality_score * 100)}%`}
            </dd>
          </div>
          <div>
            <dt>Pages</dt>
            <dd>{document.page_count ?? "—"}</dd>
          </div>
          <div>
            <dt>Language</dt>
            <dd>{document.language ?? "—"}</dd>
          </div>
          <div>
            <dt>Extraction</dt>
            <dd>{document.extraction_method ?? "—"}</dd>
          </div>
        </dl>
        <section>
          <h3>Processing lifecycle</h3>
          <ol className="lifecycle-list">
            {stages.map((stage, index) => {
              const completed = activeIndex > index || document.status === "ready";
              const active = activeIndex === index;
              return (
                <li key={stage} className={completed ? "completed" : active ? "active" : "pending"}>
                  {completed ? <Check aria-hidden="true" /> : <Circle aria-hidden="true" />}
                  <span>{stage}</span>
                  <small>{completed ? "Completed" : active ? "In progress" : "Pending"}</small>
                </li>
              );
            })}
          </ol>
        </section>
        {document.error_message && (
          <section className="failure-box">
            <h3>Processing failure</h3>
            <p>{document.error_message}</p>
          </section>
        )}
      </div>
    </aside>
  );
}

import { useEffect, useMemo, useState } from "react";
import { ClipboardList, Copy, Search, X } from "lucide-react";
import { operatorApiClient, type AuditEvent } from "../../api/operatorApiClient";
import { operatorQueryKeys } from "../../api/operatorConsoleQueries";
import { EmptyState, ErrorState, LoadingState } from "../../components/QueryStatePanel";
import { StatusBadge } from "../../components/StatusBadge";
import { formatDate, shortId } from "../../shared/formatters";
import { useQuery } from "@tanstack/react-query";

type OutcomeFilter = "all" | AuditEvent["outcome"];

function detailPreview(detail: AuditEvent["detail"]) {
  const json = JSON.stringify(detail);
  if (!json || json === "{}") return "No extra detail";
  return json.length > 72 ? `${json.slice(0, 72)}…` : json;
}

function prettyDetail(detail: AuditEvent["detail"]) {
  return JSON.stringify(detail, null, 2);
}

export function AuditHistory() {
  const [query, setQuery] = useState("");
  const [outcome, setOutcome] = useState<OutcomeFilter>("all");
  const [selected, setSelected] = useState<AuditEvent | null>(null);
  const audit = useQuery({
    queryKey: operatorQueryKeys.audit,
    queryFn: () => operatorApiClient.getAuditEvents(),
    refetchInterval: 30_000,
  });
  const filtered = useMemo(() => {
    const events = audit.data ?? [];
    const needle = query.trim().toLowerCase();
    return events.filter((event) => {
      if (outcome !== "all" && event.outcome !== outcome) return false;
      if (!needle) return true;
      const haystack = [
        event.event_type,
        event.outcome,
        event.actor_type,
        event.actor_id,
        event.resource_type,
        event.resource_id,
        event.project_id,
        JSON.stringify(event.detail),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(needle);
    });
  }, [audit.data, outcome, query]);

  if (audit.isPending) return <LoadingState label="Loading audit history" />;
  if (audit.isError) return <ErrorState error={audit.error} retry={() => void audit.refetch()} />;
  if (!audit.data.length)
    return (
      <EmptyState
        title="No audit events"
        detail="Operator and durable-job activity will appear here."
      />
    );

  return (
    <section className="panel">
      <div className="panel__heading">
        <div>
          <h2>Recent audit history</h2>
          <p>Newest deployment events first. Open a row to inspect the full payload.</p>
        </div>
        <ClipboardList size={20} aria-hidden="true" />
      </div>
      <div className="audit-toolbar">
        <label className="field-control field-control--grow">
          <span className="sr-only">Search audit events</span>
          <span className="audit-search">
            <Search size={15} aria-hidden="true" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search event, actor, resource, or detail"
            />
          </span>
        </label>
        <label className="field-control">
          <span>Outcome</span>
          <select
            value={outcome}
            onChange={(event) => setOutcome(event.target.value as OutcomeFilter)}
          >
            <option value="all">All outcomes</option>
            <option value="success">Success</option>
            <option value="failure">Failure</option>
            <option value="deferred">Deferred</option>
          </select>
        </label>
      </div>
      {filtered.length === 0 ? (
        <EmptyState
          compact
          title="No matching events"
          detail="Try a different search or outcome filter."
        />
      ) : (
        <div className="table-scroll">
          <table className="audit-table">
            <caption className="sr-only">Recent audit history</caption>
            <thead>
              <tr>
                <th>Time</th>
                <th>Event</th>
                <th>Outcome</th>
                <th>Actor</th>
                <th>Resource</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((event) => (
                <tr
                  key={event.id}
                  className="audit-row"
                  tabIndex={0}
                  onClick={() => setSelected(event)}
                  onKeyDown={(keyboard) => {
                    if (keyboard.key === "Enter" || keyboard.key === " ") {
                      keyboard.preventDefault();
                      setSelected(event);
                    }
                  }}
                >
                  <td>{formatDate(event.created_at)}</td>
                  <td>
                    <strong>{event.event_type}</strong>
                    <small>
                      {event.resource_type} · {shortId(event.resource_id)}
                      {event.project_id ? ` · project ${shortId(event.project_id)}` : ""}
                    </small>
                  </td>
                  <td>
                    <StatusBadge status={event.outcome} />
                  </td>
                  <td>
                    {event.actor_type}
                    {event.actor_id ? (
                      <>
                        <small>{event.actor_id}</small>
                      </>
                    ) : null}
                  </td>
                  <td>
                    {event.resource_type}
                    <small>{shortId(event.resource_id)}</small>
                  </td>
                  <td>
                    <span className="audit-detail-cell">
                      <span className="audit-preview">{detailPreview(event.detail)}</span>
                      <button
                        className="table-link"
                        type="button"
                        onClick={(click) => {
                          click.stopPropagation();
                          setSelected(event);
                        }}
                      >
                        Inspect
                      </button>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {selected && (
        <AuditEventInspector event={selected} onClose={() => setSelected(null)} />
      )}
    </section>
  );
}

function AuditEventInspector({ event, onClose }: { event: AuditEvent; onClose: () => void }) {
  const payload = prettyDetail(event.detail);
  useEffect(() => {
    const onKey = (keyboard: KeyboardEvent) => {
      if (keyboard.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <section
      className="handoff-panel"
      role="dialog"
      aria-modal="true"
      aria-labelledby="audit-inspector-title"
      onClick={onClose}
    >
      <div
        className="handoff-panel__card audit-inspector"
        onClick={(click) => click.stopPropagation()}
      >
        <div className="audit-inspector__header">
          <div>
            <p className="eyebrow">Audit event</p>
            <h2 id="audit-inspector-title">{event.event_type}</h2>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="Close">
            <X size={17} aria-hidden="true" />
          </button>
        </div>
        <dl className="fact-grid">
          <div>
            <dt>Time</dt>
            <dd>{formatDate(event.created_at)}</dd>
          </div>
          <div>
            <dt>Outcome</dt>
            <dd>
              <StatusBadge status={event.outcome} />
            </dd>
          </div>
          <div>
            <dt>Actor</dt>
            <dd>
              {event.actor_type}
              {event.actor_id ? ` · ${event.actor_id}` : ""}
            </dd>
          </div>
          <div>
            <dt>Resource</dt>
            <dd>
              {event.resource_type} · {event.resource_id}
            </dd>
          </div>
          <div>
            <dt>Project</dt>
            <dd>{event.project_id ?? "—"}</dd>
          </div>
          <div>
            <dt>Organization</dt>
            <dd>{event.organization_id ?? "—"}</dd>
          </div>
        </dl>
        <label className="field-control">
          <span>Detail payload</span>
          <pre className="handoff-code">{payload}</pre>
        </label>
        <div className="button-row">
          <button
            className="button button--primary"
            type="button"
            onClick={() => void navigator.clipboard.writeText(payload)}
          >
            <Copy size={15} /> Copy JSON
          </button>
          <button className="button button--secondary" type="button" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </section>
  );
}

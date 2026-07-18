import { useQuery } from "@tanstack/react-query";
import { ClipboardList } from "lucide-react";
import { operatorApiClient } from "../../api/operatorApiClient";
import { operatorQueryKeys } from "../../api/operatorConsoleQueries";
import { EmptyState, ErrorState, LoadingState } from "../../components/QueryStatePanel";
import { StatusBadge } from "../../components/StatusBadge";
import { formatDate, shortId } from "../../shared/formatters";

export function AuditHistory() {
  const audit = useQuery({
    queryKey: operatorQueryKeys.audit,
    queryFn: () => operatorApiClient.getAuditEvents(),
    refetchInterval: 30_000,
  });
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
          <p>Newest deployment events first</p>
        </div>
        <ClipboardList size={20} aria-hidden="true" />
      </div>
      <div className="table-scroll">
        <table>
          <caption className="sr-only">Recent audit history</caption>
          <thead>
            <tr>
              <th>Time</th>
              <th>Event</th>
              <th>Outcome</th>
              <th>Actor</th>
              <th>Resource</th>
              <th>Project</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {audit.data.map((event) => (
              <tr key={event.id}>
                <td>{formatDate(event.created_at)}</td>
                <td>
                  <strong>{event.event_type}</strong>
                </td>
                <td>
                  <StatusBadge status={event.outcome} />
                </td>
                <td>
                  {event.actor_type}
                  {event.actor_id ? ` · ${event.actor_id}` : ""}
                </td>
                <td>
                  {event.resource_type} · {shortId(event.resource_id)}
                </td>
                <td>{shortId(event.project_id)}</td>
                <td>
                  <code className="detail-json">{JSON.stringify(event.detail)}</code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

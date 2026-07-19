import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCcw, Webhook } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import { operatorApiClient, type WebhookEventType } from "../../api/operatorApiClient";
import { operatorQueryKeys, useProjects } from "../../api/operatorConsoleQueries";
import { ProjectSelector } from "../../components/ProjectSelector";
import { EmptyState, ErrorState, LoadingState } from "../../components/QueryStatePanel";
import { StatusBadge } from "../../components/StatusBadge";
import { formatDate, shortId } from "../../shared/formatters";

const eventTypes: WebhookEventType[] = [
  "document.processing.succeeded.v1",
  "document.processing.failed.v1",
  "document.indexing.succeeded.v1",
  "document.indexing.failed.v1",
];

export function WebhookDeliveryInspection() {
  const projects = useProjects();
  const queryClient = useQueryClient();
  const [projectId, setProjectId] = useState("");
  const [url, setUrl] = useState("");
  const [signingSecret, setSigningSecret] = useState("");
  const [selectedDeliveryId, setSelectedDeliveryId] = useState("");

  useEffect(() => {
    if (!projectId && projects.data?.items[0]) setProjectId(projects.data.items[0].id);
  }, [projectId, projects.data]);

  const endpoints = useQuery({
    queryKey: operatorQueryKeys.webhookEndpoints(projectId),
    queryFn: () => operatorApiClient.getWebhookEndpoints(projectId),
    enabled: Boolean(projectId),
  });
  const deliveries = useQuery({
    queryKey: operatorQueryKeys.webhookDeliveries(projectId),
    queryFn: () => operatorApiClient.getWebhookDeliveries(projectId),
    enabled: Boolean(projectId),
    refetchInterval: 5_000,
  });
  const deliveryDetail = useQuery({
    queryKey: operatorQueryKeys.webhookDelivery(projectId, selectedDeliveryId),
    queryFn: () => operatorApiClient.getWebhookDelivery(projectId, selectedDeliveryId),
    enabled: Boolean(projectId && selectedDeliveryId),
  });
  const create = useMutation({
    mutationFn: () => operatorApiClient.createWebhookEndpoint(projectId, url, eventTypes),
    onSuccess: async (endpoint) => {
      setSigningSecret(endpoint.signing_secret);
      setUrl("");
      await queryClient.invalidateQueries({
        queryKey: operatorQueryKeys.webhookEndpoints(projectId),
      });
    },
  });
  const setStatus = useMutation({
    mutationFn: ({ endpointId, enabled }: { endpointId: string; enabled: boolean }) =>
      operatorApiClient.setWebhookEndpointStatus(projectId, endpointId, enabled),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: operatorQueryKeys.webhookEndpoints(projectId),
      });
    },
  });
  const replay = useMutation({
    mutationFn: (deliveryId: string) =>
      operatorApiClient.replayWebhookDelivery(projectId, deliveryId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: operatorQueryKeys.webhookDeliveries(projectId),
      });
    },
  });

  if (projects.isPending) return <LoadingState label="Loading projects" />;
  if (projects.isError)
    return <ErrorState error={projects.error} retry={() => void projects.refetch()} />;
  if (!projects.data.items.length)
    return (
      <EmptyState title="No projects" detail="Create a Project before configuring webhooks." />
    );

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setSigningSecret("");
    create.mutate();
  };

  return (
    <div className="page-stack webhooks-page">
      <section className="panel webhook-create-panel">
        <div className="toolbar webhook-create-toolbar">
          <ProjectSelector
            projects={projects.data.items}
            value={projectId}
            onChange={(value) => {
              setProjectId(value);
              setSelectedDeliveryId("");
            }}
          />
          <form className="webhook-create-form" onSubmit={submit}>
            <label className="field-control field-control--grow">
              <span>HTTPS receiver URL</span>
              <input
                type="url"
                value={url}
                required
                placeholder="https://customer.example.com/webhooks/ape"
                onChange={(event) => setUrl(event.target.value)}
              />
            </label>
            <button className="button button--primary" type="submit" disabled={create.isPending}>
              <Webhook size={16} aria-hidden="true" />{" "}
              {create.isPending ? "Creating…" : "Add endpoint"}
            </button>
          </form>
        </div>
        <p className="helper-text">
          New endpoints subscribe to the four versioned document processing and indexing outcomes.
        </p>
        {signingSecret && (
          <div className="success-note" role="status">
            Store this signing secret now: <code>{signingSecret}</code>
          </div>
        )}
        {create.isError && <p className="error-note">{create.error.message}</p>}
      </section>

      <section className="panel webhooks-panel">
        <div className="panel__heading">
          <div>
            <h2>Endpoints</h2>
            <p>Disable receivers before maintenance or key rotation</p>
          </div>
        </div>
        {endpoints.isPending ? (
          <LoadingState label="Loading webhook endpoints" />
        ) : endpoints.isError ? (
          <ErrorState error={endpoints.error} retry={() => void endpoints.refetch()} />
        ) : !endpoints.data.items.length ? (
          <EmptyState
            title="No webhook endpoints"
            detail="Add the first customer receiver above."
          />
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>URL</th>
                  <th>Events</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {endpoints.data.items.map((endpoint) => (
                  <tr key={endpoint.id}>
                    <td>
                      <strong>{endpoint.url}</strong>
                    </td>
                    <td>{endpoint.event_types.length}</td>
                    <td>
                      <StatusBadge status={endpoint.is_enabled ? "enabled" : "disabled"} />
                    </td>
                    <td>{formatDate(endpoint.created_at)}</td>
                    <td>
                      <button
                        className="button button--secondary"
                        type="button"
                        onClick={() =>
                          setStatus.mutate({
                            endpointId: endpoint.id,
                            enabled: !endpoint.is_enabled,
                          })
                        }
                      >
                        {endpoint.is_enabled ? "Disable" : "Enable"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel webhooks-panel">
        <div className="panel__heading">
          <div>
            <h2>Delivery history</h2>
            <p>Newest attempts and replays first</p>
          </div>
        </div>
        {deliveries.isPending ? (
          <LoadingState label="Loading webhook deliveries" />
        ) : deliveries.isError ? (
          <ErrorState error={deliveries.error} retry={() => void deliveries.refetch()} />
        ) : !deliveries.data.items.length ? (
          <EmptyState
            title="No deliveries"
            detail="Document outcome events will appear after a subscribed job completes."
          />
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Created</th>
                  <th>Event</th>
                  <th>Status</th>
                  <th>Attempts</th>
                  <th>HTTP</th>
                  <th>Inspect</th>
                  <th>Replay</th>
                </tr>
              </thead>
              <tbody>
                {deliveries.data.items.map((delivery) => (
                  <tr key={delivery.id}>
                    <td>{formatDate(delivery.created_at)}</td>
                    <td>{shortId(delivery.event_id)}</td>
                    <td>
                      <StatusBadge status={delivery.state} />
                    </td>
                    <td>
                      {delivery.attempt_count}/{delivery.max_attempts}
                    </td>
                    <td>{delivery.last_status_code ?? delivery.last_error ?? "—"}</td>
                    <td>
                      <button
                        className="button button--secondary"
                        type="button"
                        onClick={() => setSelectedDeliveryId(delivery.id)}
                      >
                        Inspect
                      </button>
                    </td>
                    <td>
                      <button
                        className="button button--secondary"
                        type="button"
                        disabled={replay.isPending}
                        onClick={() => replay.mutate(delivery.id)}
                      >
                        <RotateCcw size={14} aria-hidden="true" /> Replay
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {replay.isError && <p className="error-note">{replay.error.message}</p>}
      </section>

      {selectedDeliveryId && (
        <section className="panel webhooks-panel" aria-label="Webhook delivery detail">
          <div className="panel__heading">
            <div>
              <h2>Delivery detail</h2>
              <p>Immutable outcome payload and completed HTTP attempts</p>
            </div>
          </div>
          {deliveryDetail.isPending ? (
            <LoadingState label="Loading webhook delivery detail" />
          ) : deliveryDetail.isError ? (
            <ErrorState error={deliveryDetail.error} retry={() => void deliveryDetail.refetch()} />
          ) : deliveryDetail.data ? (
            <>
              <div className="build-pointer-grid">
                <article>
                  <span>Event type</span>
                  <strong>{deliveryDetail.data.event.event_type}</strong>
                  <small>Event {deliveryDetail.data.event.id}</small>
                </article>
                <article>
                  <span>Source</span>
                  <strong>{deliveryDetail.data.event.source_type}</strong>
                  <small>{deliveryDetail.data.event.source_id}</small>
                </article>
                <article>
                  <span>Last outcome</span>
                  <strong>{deliveryDetail.data.last_status_code ?? "No HTTP response"}</strong>
                  <small>{deliveryDetail.data.last_error ?? "No delivery error"}</small>
                </article>
              </div>
              {deliveryDetail.data.attempts.length ? (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Attempt</th>
                        <th>Time</th>
                        <th>HTTP</th>
                        <th>Latency</th>
                        <th>Error / response</th>
                      </tr>
                    </thead>
                    <tbody>
                      {deliveryDetail.data.attempts.map((attempt) => (
                        <tr key={attempt.id}>
                          <td>{attempt.attempt_number}</td>
                          <td>{formatDate(attempt.attempted_at)}</td>
                          <td>{attempt.status_code ?? "—"}</td>
                          <td>{attempt.latency_ms == null ? "—" : `${attempt.latency_ms} ms`}</td>
                          <td>{attempt.error ?? attempt.response_excerpt ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <EmptyState
                  title="No attempts yet"
                  detail="The dispatcher has not claimed this delivery."
                />
              )}
            </>
          ) : null}
        </section>
      )}
    </div>
  );
}

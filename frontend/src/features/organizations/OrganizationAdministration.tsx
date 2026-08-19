import { Building2, Copy, Download, KeyRound, Plus, RotateCw } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  operatorApiClient,
  type ApiKeySecret,
  type Organization,
} from "../../api/operatorApiClient";
import { apiUrl } from "../../api/apiOrigin";
import {
  useApiKeys,
  useOrganizationProjects,
  useOrganizations,
  operatorQueryKeys,
} from "../../api/operatorConsoleQueries";
import { EmptyState, ErrorState, LoadingState } from "../../components/QueryStatePanel";
import { StatusBadge } from "../../components/StatusBadge";
import { formatDate, shortId } from "../../shared/formatters";

function organizationStatus(organization: Organization) {
  if (organization.deleted_at) return "archived";
  return organization.is_active ? "active" : "disabled";
}

export function OrganizationAdministration() {
  const queryClient = useQueryClient();
  const organizations = useOrganizations();
  const [params, setParams] = useSearchParams();
  const requestedId = params.get("organization") ?? "";
  const [creating, setCreating] = useState(false);
  const invalidateAdministration = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: operatorQueryKeys.organizations }),
      queryClient.invalidateQueries({ queryKey: operatorQueryKeys.projects }),
    ]);
  };

  const selectedId = useMemo(() => {
    const items = organizations.data?.items ?? [];
    return items.some((item) => item.id === requestedId) ? requestedId : (items[0]?.id ?? "");
  }, [organizations.data, requestedId]);
  const selected = organizations.data?.items.find((item) => item.id === selectedId);

  useEffect(() => {
    if (selectedId && requestedId !== selectedId) {
      setParams({ organization: selectedId }, { replace: true });
    }
  }, [requestedId, selectedId, setParams]);

  if (organizations.isPending) return <LoadingState label="Loading Organizations" />;
  if (organizations.isError)
    return <ErrorState error={organizations.error} retry={() => void organizations.refetch()} />;

  return (
    <div className="admin-workspace">
      <section className="panel admin-rail">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Client boundary</p>
            <h2>Organizations</h2>
          </div>
          <button className="icon-button" type="button" onClick={() => setCreating(true)}>
            <Plus size={17} aria-hidden="true" />
            <span className="sr-only">Create Organization</span>
          </button>
        </div>
        <p className="muted-copy">
          Organizations own client identity, credentials, and Project ownership.
        </p>
        {creating && (
          <CreateOrganization
            onCancel={() => setCreating(false)}
            onCreated={(organization) => {
              setCreating(false);
              void invalidateAdministration();
              setParams({ organization: organization.id });
            }}
          />
        )}
        {organizations.data.items.length === 0 ? (
          <EmptyState
            compact
            title="No clients yet"
            detail="Create the first Organization to begin."
          />
        ) : (
          <div className="admin-rail__list">
            {organizations.data.items.map((organization) => (
              <button
                key={organization.id}
                type="button"
                className={
                  organization.id === selectedId ? "rail-card rail-card--active" : "rail-card"
                }
                onClick={() => setParams({ organization: organization.id })}
              >
                <span className="rail-card__icon">
                  <Building2 size={17} />
                </span>
                <span>
                  <strong>{organization.name}</strong>
                  <small>{shortId(organization.id)}</small>
                </span>
                <StatusBadge status={organizationStatus(organization)} />
              </button>
            ))}
          </div>
        )}
      </section>
      <section className="admin-detail">
        {selected ? (
          <OrganizationDetail
            organization={selected}
            onChanged={() => void invalidateAdministration()}
          />
        ) : (
          <EmptyState
            compact
            title="Select an Organization"
            detail="Client details, keys, and associated Projects appear here."
          />
        )}
      </section>
    </div>
  );
}

function CreateOrganization({
  onCancel,
  onCreated,
}: {
  onCancel: () => void;
  onCreated: (organization: Organization) => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      onCreated(await operatorApiClient.createOrganization(name, description));
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="stack-form compact-form" onSubmit={(event) => void submit(event)}>
      <label className="field-control">
        <span>Client name</span>
        <input
          required
          maxLength={255}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <label className="field-control">
        <span>Description</span>
        <textarea
          rows={3}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
      </label>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <div className="button-row">
        <button className="button button--primary" disabled={busy} type="submit">
          Create
        </button>
        <button className="button button--secondary" type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function OrganizationDetail({
  organization,
  onChanged,
}: {
  organization: Organization;
  onChanged: () => void;
}) {
  const projects = useOrganizationProjects(organization.id);
  const keys = useApiKeys(organization.id);
  const [name, setName] = useState(organization.name);
  const [description, setDescription] = useState(organization.description ?? "");
  const [keyName, setKeyName] = useState("");
  const [handoff, setHandoff] = useState<ApiKeySecret | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    setName(organization.name);
    setDescription(organization.description ?? "");
    setHandoff(null);
    setError("");
  }, [organization]);

  const run = async (label: string, action: () => Promise<unknown>) => {
    setBusy(label);
    setError("");
    try {
      await action();
      onChanged();
      await Promise.all([projects.refetch(), keys.refetch()]);
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy("");
    }
  };

  const issueKey = async (event: FormEvent) => {
    event.preventDefault();
    setBusy("key-create");
    setError("");
    try {
      const created = await operatorApiClient.createApiKey(organization.id, keyName);
      setHandoff(created);
      setKeyName("");
      await keys.refetch();
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy("");
    }
  };

  return (
    <>
      <section className="panel detail-hero">
        <div>
          <p className="eyebrow">Organization · {shortId(organization.id)}</p>
          <h2>{organization.name}</h2>
          <p>{organization.description || "No client description."}</p>
        </div>
        <StatusBadge status={organizationStatus(organization)} />
      </section>
      {error && (
        <div className="failure-box" role="alert">
          {error}
        </div>
      )}
      <section className="panel credentials-panel">
        <div className="section-heading">
          <div>
            <h3>API keys</h3>
            <p>Replacement keys remain active beside the old key until explicit revocation.</p>
          </div>
          <KeyRound size={20} aria-hidden="true" />
        </div>
        <div className="panel-body">
          <form className="inline-form" onSubmit={(event) => void issueKey(event)}>
            <label className="field-control field-control--grow">
              <span>New key name</span>
              <input
                required
                maxLength={64}
                value={keyName}
                onChange={(event) => setKeyName(event.target.value)}
                placeholder="production"
              />
            </label>
            <button
              className="button button--primary"
              type="submit"
              disabled={
                !organization.is_active || Boolean(organization.deleted_at) || Boolean(busy)
              }
            >
              Issue named key
            </button>
          </form>
          {keys.isPending ? (
            <LoadingState label="Loading API keys" />
          ) : keys.data?.items.length ? (
            <div className="credential-list">
              {keys.data.items.map((key) => (
                <article key={key.id} className="credential-card">
                  <div className="credential-card__header">
                    <div>
                      <strong>{key.name}</strong>
                      <small>
                        {key.key_prefix} · {shortId(key.id)}
                      </small>
                      <small className="credential-card__dates">
                        Created {formatDate(key.created_at)} · Last used{" "}
                        {key.last_used_at ? formatDate(key.last_used_at) : "never"}
                      </small>
                    </div>
                    <StatusBadge status={key.status} />
                  </div>
                  {key.status === "active" && (
                    <div className="button-row">
                      <button
                        className="button button--secondary"
                        type="button"
                        disabled={!organization.is_active || Boolean(busy)}
                        onClick={() => {
                          void (async () => {
                            const replacementName = window.prompt(
                              "Replacement key name",
                              `${key.name}-next`,
                            );
                            if (!replacementName) return;
                            setBusy(`rotate-${key.id}`);
                            setError("");
                            try {
                              setHandoff(
                                await operatorApiClient.rotateApiKey(
                                  organization.id,
                                  key.id,
                                  replacementName,
                                ),
                              );
                              await keys.refetch();
                            } catch (caught) {
                              setError((caught as Error).message);
                            } finally {
                              setBusy("");
                            }
                          })();
                        }}
                      >
                        <RotateCw size={14} /> Replace
                      </button>
                      <button
                        className="button button--danger"
                        type="button"
                        disabled={!organization.is_active || Boolean(busy)}
                        onClick={() => {
                          const confirmed = window.confirm(
                            `Emergency rotation will revoke ${key.name} immediately. Continue?`,
                          );
                          if (!confirmed) return;
                          const replacementName = window.prompt(
                            "Emergency replacement key name",
                            `${key.name}-emergency`,
                          );
                          if (!replacementName) return;
                          void (async () => {
                            setBusy(`emergency-${key.id}`);
                            setError("");
                            try {
                              setHandoff(
                                await operatorApiClient.rotateApiKey(
                                  organization.id,
                                  key.id,
                                  replacementName,
                                  true,
                                ),
                              );
                              await keys.refetch();
                            } catch (caught) {
                              setError((caught as Error).message);
                            } finally {
                              setBusy("");
                            }
                          })();
                        }}
                      >
                        Emergency rotate
                      </button>
                      <button
                        className="button button--danger"
                        type="button"
                        disabled={Boolean(busy)}
                        onClick={() => {
                          if (
                            window.confirm(
                              `Revoke ${key.name}? Requests using it will fail immediately.`,
                            )
                          )
                            void run(`revoke-${key.id}`, () =>
                              operatorApiClient.revokeApiKey(organization.id, key.id),
                            );
                        }}
                      >
                        Revoke
                      </button>
                    </div>
                  )}
                </article>
              ))}
            </div>
          ) : (
            <EmptyState
              compact
              title="No API keys"
              detail="Issue a named credential for this client."
            />
          )}
        </div>
      </section>
      <div className="detail-grid">
        <section className="panel">
          <div className="section-heading">
            <h3>Client record</h3>
            <span>Updated {formatDate(organization.updated_at)}</span>
          </div>
          <form
            className="stack-form panel-body"
            onSubmit={(event) => {
              event.preventDefault();
              void run("save", () =>
                operatorApiClient.updateOrganization(organization.id, name, description),
              );
            }}
          >
            <label className="field-control">
              <span>Name</span>
              <input required value={name} onChange={(event) => setName(event.target.value)} />
            </label>
            <label className="field-control">
              <span>Description</span>
              <textarea
                rows={3}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
            </label>
            <div className="button-row">
              {!organization.deleted_at && (
                <button className="button button--primary" disabled={Boolean(busy)} type="submit">
                  Save changes
                </button>
              )}
              {!organization.deleted_at && (
                <button
                  className="button button--secondary"
                  type="button"
                  disabled={Boolean(busy)}
                  onClick={() =>
                    void run("status", () =>
                      operatorApiClient.setOrganizationStatus(
                        organization.id,
                        !organization.is_active,
                      ),
                    )
                  }
                >
                  {organization.is_active ? "Disable access" : "Enable access"}
                </button>
              )}
              {!organization.deleted_at ? (
                <button
                  className="button button--danger"
                  type="button"
                  disabled={Boolean(busy)}
                  onClick={() => {
                    if (
                      window.confirm(
                        "Archive this Organization? Credentials will stop authenticating.",
                      )
                    ) {
                      void run("archive", () =>
                        operatorApiClient.archiveOrganization(organization.id),
                      );
                    }
                  }}
                >
                  Archive
                </button>
              ) : (
                <button
                  className="button button--primary"
                  type="button"
                  disabled={Boolean(busy)}
                  onClick={() =>
                    void run("restore", () =>
                      operatorApiClient.restoreOrganization(organization.id),
                    )
                  }
                >
                  Restore disabled
                </button>
              )}
            </div>
          </form>
        </section>
        <section className="panel">
          <div className="section-heading">
            <h3>Associated Projects</h3>
            <span>{projects.data?.total ?? 0} total</span>
          </div>
          <div className="panel-body">
            {projects.isPending ? (
              <LoadingState label="Loading Projects" />
            ) : projects.data?.items.length ? (
              <div className="record-list">
                {projects.data.items.map((project) => (
                  <Link
                    key={project.id}
                    className="record-row"
                    to={`/projects?project=${project.id}`}
                  >
                    <span>
                      <strong>{project.name}</strong>
                      <small>{shortId(project.id)}</small>
                    </span>
                    <StatusBadge
                      status={
                        project.deleted_at ? "archived" : project.is_active ? "active" : "disabled"
                      }
                    />
                  </Link>
                ))}
              </div>
            ) : (
              <EmptyState
                compact
                title="No Projects"
                detail="Create a Project from canonical Project administration."
              />
            )}
          </div>
        </section>
      </div>
      {handoff && (
        <CredentialHandoff
          organizationName={organization.name}
          credential={handoff}
          onClose={() => setHandoff(null)}
        />
      )}
    </>
  );
}

function CredentialHandoff({
  organizationName,
  credential,
  onClose,
}: {
  organizationName: string;
  credential: ApiKeySecret;
  onClose: () => void;
}) {
  const apiBaseUrl = new URL(apiUrl("/api/v1"), window.location.origin)
    .toString()
    .replace(/\/$/, "");
  const bundle = [
    `Organization: ${organizationName}`,
    `Key name: ${credential.name}`,
    `API base URL: ${apiBaseUrl}`,
    `Authorization: Bearer ${credential.secret}`,
    "",
    `curl -H "Authorization: Bearer ${credential.secret}" "${apiBaseUrl}/projects"`,
  ].join("\n");
  const copy = () => void navigator.clipboard.writeText(bundle);
  const download = () => {
    const url = URL.createObjectURL(new Blob([bundle], { type: "text/plain" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${organizationName.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}-${credential.name}-handoff.txt`;
    anchor.click();
    URL.revokeObjectURL(url);
  };
  return (
    <section
      className="handoff-panel"
      role="dialog"
      aria-modal="true"
      aria-labelledby="handoff-title"
    >
      <div className="handoff-panel__card">
        <p className="eyebrow">One-time credential handoff</p>
        <h2 id="handoff-title">Copy this secret now</h2>
        <p>The plaintext secret is returned only once and is not saved in browser storage.</p>
        <dl className="fact-grid">
          <div>
            <dt>Organization</dt>
            <dd>{organizationName}</dd>
          </div>
          <div>
            <dt>Key</dt>
            <dd>{credential.name}</dd>
          </div>
          <div>
            <dt>API base URL</dt>
            <dd>{apiBaseUrl}</dd>
          </div>
        </dl>
        <label className="field-control">
          <span>Authorization header</span>
          <input readOnly value={`Bearer ${credential.secret}`} />
        </label>
        <pre className="handoff-code">{bundle.split("\n").at(-1)}</pre>
        <div className="button-row">
          <button className="button button--primary" type="button" onClick={copy}>
            <Copy size={15} /> Copy bundle
          </button>
          <button className="button button--secondary" type="button" onClick={download}>
            <Download size={15} /> Download locally
          </button>
          <button className="button button--secondary" type="button" onClick={onClose}>
            I saved it
          </button>
        </div>
      </div>
    </section>
  );
}

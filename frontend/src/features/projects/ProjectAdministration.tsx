import { Database, FileClock, Plus, Settings2, ShieldCheck, X } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  operatorApiClient,
  type EffectiveProjectAIConfig,
  type Organization,
  type Project,
  type ProjectAIConfigRevision,
  type ProjectOwnershipPreflight,
  type ProviderCapability,
  type SourceRevisionCreate,
} from "../../api/operatorApiClient";
import {
  useActiveConfiguration,
  useAllOperatorProjects,
  useDocuments,
  useOrganizations,
  useSourceActivations,
  useSourceHistory,
  useSourceState,
  useUploadDocument,
  operatorQueryKeys,
} from "../../api/operatorConsoleQueries";
import { EmptyState, ErrorState, LoadingState } from "../../components/QueryStatePanel";
import { StatusBadge } from "../../components/StatusBadge";
import { formatBytes, formatDate, shortId } from "../../shared/formatters";
import {
  FieldHint,
  InheritanceToggle,
  PROJECT_AI_FIELD_HINTS,
  ProjectAISettingsFields,
  buildSparseProjectConfig,
  configFormFromEffective,
  configFormFromDeployment,
  configOverridesFromStored,
  emptyProjectConfigForm,
  inheritedFormFromEffective,
  inheritedProjectConfig,
  sparseHasOverrides,
  type ProjectConfigForm,
  type ProjectConfigOverride,
  type ProjectConfigOverrides,
} from "./ProjectAISettingsFields";

const tabs = ["details", "ai-config", "sources", "history"] as const;
type ProjectTab = (typeof tabs)[number];

function projectStatus(project: Project) {
  if (project.deleted_at) return "archived";
  return project.is_active ? "active" : "disabled";
}

const originFieldLabels: Record<string, string> = {
  "llm.provider": "Provider",
  "llm.model": "Model",
  "llm.temperature": "Temperature",
  "llm.max_tokens": "Max tokens",
  "retrieval.strategy": "Strategy",
  "retrieval.top_k": "Top K",
  "retrieval.rerank_mode": "Rerank",
  "retrieval.query_translation_enabled": "Query translation",
  "retrieval.semantic_evidence_score_threshold": "Evidence threshold",
  "chat.include_citations": "Citations",
  domain_instructions: "Project instructions",
  source_policy_mode: "Source policy",
};

function originFieldLabel(field: string) {
  return originFieldLabels[field] ?? (field.split(".").pop() ?? field).replaceAll("_", " ");
}

function originKindLabel(origin: string) {
  if (origin === "global") return "deployment";
  if (origin === "project") return "Project";
  return origin.replaceAll("_", " ");
}

function OriginSummary({ origins }: { origins: Record<string, string> }) {
  const overrides = Object.entries(origins).filter(([, origin]) => origin !== "global");
  if (overrides.length === 0) {
    return (
      <p className="origin-summary">
        All settings inherit deployment defaults. This strip only lists values this Project
        overrides.
      </p>
    );
  }
  return (
    <div className="origin-summary">
      <p>
        {overrides.length} Project override{overrides.length === 1 ? "" : "s"}
      </p>
      <div className="origin-pills">
        {overrides.map(([field, origin]) => (
          <span key={field} title={field}>
            {originFieldLabel(field)} · {originKindLabel(origin)}
          </span>
        ))}
      </div>
    </div>
  );
}

export function ProjectAdministration() {
  const queryClient = useQueryClient();
  const projects = useAllOperatorProjects();
  const organizations = useOrganizations();
  const [params, setParams] = useSearchParams();
  const requestedId = params.get("project") ?? "";
  const requestedTab = params.get("section") as ProjectTab | null;
  const tab = requestedTab && tabs.includes(requestedTab) ? requestedTab : "details";
  const [creating, setCreating] = useState(false);
  const [aiConfigWarning, setAiConfigWarning] = useState("");
  const [migration, setMigration] = useState<Awaited<
    ReturnType<typeof operatorApiClient.getProjectOwnershipMigration>
  > | null>(null);
  const invalidateAdministration = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: operatorQueryKeys.projects }),
      queryClient.invalidateQueries({ queryKey: operatorQueryKeys.organizations }),
      queryClient.invalidateQueries({ queryKey: operatorQueryKeys.ownershipMigration }),
    ]);
  }, [queryClient]);
  useEffect(() => {
    void queryClient.prefetchQuery({
      queryKey: operatorQueryKeys.configuration,
      queryFn: operatorApiClient.getConfiguration,
      staleTime: 60_000,
    });
  }, [queryClient]);

  const projectId = useMemo(() => {
    const items = projects.data?.items ?? [];
    return items.some((item) => item.id === requestedId) ? requestedId : (items[0]?.id ?? "");
  }, [projects.data, requestedId]);
  const selected = projects.data?.items.find((item) => item.id === projectId);

  useEffect(() => {
    void operatorApiClient.getProjectOwnershipMigration().then(setMigration);
  }, [projects.data]);
  useEffect(() => {
    if (projectId && projectId !== requestedId) {
      setParams({ project: projectId, section: tab }, { replace: true });
    }
  }, [projectId, requestedId, setParams, tab]);

  if (projects.isPending || organizations.isPending)
    return <LoadingState label="Loading Projects" />;
  if (projects.isError)
    return <ErrorState error={projects.error} retry={() => void projects.refetch()} />;
  if (organizations.isError)
    return <ErrorState error={organizations.error} retry={() => void organizations.refetch()} />;

  const choose = (id: string, section: ProjectTab = tab) => setParams({ project: id, section });
  return (
    <div className="admin-workspace">
      <section className="panel admin-rail">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Knowledge boundary</p>
            <h2>Projects</h2>
          </div>
          <button
            className="button button--secondary button--compact"
            type="button"
            aria-label="Create Project"
            onClick={() => setCreating(true)}
          >
            <Plus size={15} aria-hidden="true" />
            New
          </button>
        </div>
        {migration && migration.legacy_unlocked_projects > 0 && (
          <div className="warning-box">
            <strong>
              {migration.legacy_unlocked_projects} legacy ownership assignment
              {migration.legacy_unlocked_projects === 1 ? "" : "s"}
            </strong>
            <span>
              {migration.default_organization_unlocked_projects} still use the default Organization.
            </span>
          </div>
        )}
        <p className="muted-copy">
          Select a Project to administer its policy, documents, and sources.
        </p>
        <div className="admin-rail__list">
          {projects.data.items.map((project) => (
            <button
              key={project.id}
              type="button"
              className={project.id === projectId ? "rail-card rail-card--active" : "rail-card"}
              onClick={() => choose(project.id)}
            >
              <span className="rail-card__icon">
                <Database size={17} />
              </span>
              <span>
                <strong>{project.name}</strong>
                <small>{shortId(project.id)}</small>
              </span>
              <StatusBadge status={projectStatus(project)} />
            </button>
          ))}
        </div>
      </section>
      <section className="admin-detail">
        {selected ? (
          <>
            {aiConfigWarning && (
              <div className="warning-box" role="status">
                <strong>Project created. AI settings were not saved.</strong>
                <span>{aiConfigWarning}</span>
                <button
                  className="button button--secondary"
                  type="button"
                  onClick={() => {
                    choose(selected.id, "ai-config");
                    setAiConfigWarning("");
                  }}
                >
                  Open AI configuration
                </button>
              </div>
            )}
            <section className="panel detail-hero">
              <div>
                <p className="eyebrow">Project · {shortId(selected.id)}</p>
                <h2>{selected.name}</h2>
                <p>{selected.description || "No Project description."}</p>
              </div>
              <div className="hero-status">
                <StatusBadge status={projectStatus(selected)} />
                <span>Source generation {selected.source_metadata_generation}</span>
              </div>
            </section>
            <nav className="section-tabs" aria-label="Project administration sections">
              {tabs.map((name) => (
                <button
                  key={name}
                  type="button"
                  className={name === tab ? "section-tab section-tab--active" : "section-tab"}
                  onClick={() => choose(selected.id, name)}
                >
                  {name === "ai-config"
                    ? "AI configuration"
                    : name[0]!.toUpperCase() + name.slice(1)}
                </button>
              ))}
            </nav>
            {tab === "details" && (
              <ProjectDetails
                project={selected}
                organizations={organizations.data.items}
                onChanged={() => void invalidateAdministration()}
              />
            )}
            {tab === "ai-config" && <ProjectConfig project={selected} />}
            {tab === "sources" && <ProjectSources project={selected} />}
            {tab === "history" && <ProjectHistory projectId={selected.id} />}
          </>
        ) : (
          <EmptyState
            compact
            title="No Projects"
            detail="Create a Project with explicit Organization ownership."
          />
        )}
      </section>
      {creating && (
        <CreateProject
          organizations={organizations.data.items.filter(
            (item) => item.is_active && !item.deleted_at,
          )}
          onCancel={() => setCreating(false)}
          onCreated={(project, extra) => {
            setCreating(false);
            void invalidateAdministration();
            choose(project.id, extra?.aiConfigError ? "ai-config" : "details");
            setAiConfigWarning(extra?.aiConfigError ?? "");
          }}
        />
      )}
    </div>
  );
}

function CreateProject({
  organizations,
  onCancel,
  onCreated,
}: {
  organizations: Organization[];
  onCancel: () => void;
  onCreated: (project: Project, extra?: { aiConfigError?: string }) => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [organizationId, setOrganizationId] = useState(organizations[0]?.id ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const configuration = useActiveConfiguration();
  const defaults = configuration.data
    ? configFormFromDeployment(configuration.data)
    : emptyProjectConfigForm;
  const [form, setForm] = useState<ProjectConfigForm>(() => defaults);
  const [overrides, setOverrides] = useState<ProjectConfigOverrides>(inheritedProjectConfig);
  const setOverride = (key: ProjectConfigOverride, enabled: boolean) => {
    setOverrides((current) => ({ ...current, [key]: enabled }));
  };
  const nameRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    nameRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [onCancel]);
  useEffect(() => {
    if (!configuration.data) return;
    const next = configFormFromDeployment(configuration.data);
    setForm((current) => {
      const merged = {
        ...current,
        provider: current.provider || next.provider,
        model: current.model || next.model,
        strategy: current.strategy || next.strategy,
      };
      setOverrides((flags) => ({
        ...flags,
        provider: merged.provider !== next.provider,
        model: merged.model !== next.model,
        strategy: merged.strategy !== next.strategy,
      }));
      return merged;
    });
  }, [configuration.data]);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const project = await operatorApiClient.createProject(name, organizationId, description);
      const sparseConfiguration = buildSparseProjectConfig({}, form, overrides, undefined);
      if (!sparseHasOverrides(sparseConfiguration)) {
        onCreated(project);
        return;
      }
      try {
        await operatorApiClient.createProjectAIConfig(
          project.id,
          sparseConfiguration,
          null,
          "Initial Project AI settings",
        );
        onCreated(project);
      } catch (caught) {
        onCreated(project, {
          aiConfigError:
            (caught as Error).message ||
            "The Project exists with inherited defaults. Retry from AI configuration.",
        });
      }
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="modal-overlay" role="presentation" onClick={onCancel}>
      <section
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-project-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="modal-card__header">
          <div>
            <p className="eyebrow">Knowledge boundary</p>
            <h2 id="create-project-title">Create Project</h2>
            <p>A Project isolates documents, retrieval, and chat for one knowledge scope.</p>
          </div>
          <button className="icon-button" type="button" aria-label="Close" onClick={onCancel}>
            <X size={16} aria-hidden="true" />
          </button>
        </header>
        <form
          id="create-project-form"
          className="modal-card__body stack-form"
          onSubmit={(event) => void submit(event)}
        >
          <label className="field-control">
            <span>Organization owner</span>
            <select
              required
              value={organizationId}
              onChange={(event) => setOrganizationId(event.target.value)}
            >
              <option value="">Select client</option>
              {organizations.map((organization) => (
                <option key={organization.id} value={organization.id}>
                  {organization.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field-control">
            <span>Project name</span>
            <input
              ref={nameRef}
              required
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
          <details className="settings-disclosure">
            <summary>Optional AI settings</summary>
            <p className="muted-copy">
              Leave Inherit global on to use the same vendor and model as this deployment (shown in
              the fields). Picking another value turns Inherit off; turning it back on restores the
              default. No AI revision is created if everything stays inherited.
            </p>
            {configuration.isPending && !configuration.data ? (
              <p className="muted-copy">Loading deployment defaults…</p>
            ) : (
              <ProjectAISettingsFields
                form={form}
                setForm={setForm}
                overrides={overrides}
                setOverride={setOverride}
                defaults={defaults}
              />
            )}
          </details>
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
        </form>
        <footer className="modal-card__footer button-row">
          <button className="button button--secondary" type="button" onClick={onCancel}>
            Cancel
          </button>
          <button
            className="button button--primary"
            form="create-project-form"
            disabled={busy || !organizationId}
          >
            Create
          </button>
        </footer>
      </section>
    </div>
  );
}

function ProjectDetails({
  project,
  organizations,
  onChanged,
}: {
  project: Project;
  organizations: Organization[];
  onChanged: () => void;
}) {
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description ?? "");
  const [target, setTarget] = useState(project.organization_id);
  const [reason, setReason] = useState("");
  const [preflight, setPreflight] = useState<ProjectOwnershipPreflight | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const owner = organizations.find((item) => item.id === project.organization_id);
  useEffect(() => {
    setName(project.name);
    setDescription(project.description ?? "");
    setTarget(project.organization_id);
    setPreflight(null);
  }, [project]);
  const run = async (label: string, action: () => Promise<unknown>) => {
    setBusy(label);
    setError("");
    try {
      await action();
      onChanged();
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy("");
    }
  };
  return (
    <>
      {error && (
        <div className="failure-box" role="alert">
          {error}
        </div>
      )}
      <div className="detail-grid">
        <section className="panel">
          <div className="section-heading">
            <h3>Project record</h3>
            <span>Created {formatDate(project.created_at)}</span>
          </div>
          <form
            className="stack-form panel-body"
            onSubmit={(event) => {
              event.preventDefault();
              void run("save", () =>
                operatorApiClient.updateProject(project.id, name, description),
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
              {!project.deleted_at && (
                <button className="button button--primary" disabled={Boolean(busy)}>
                  Save
                </button>
              )}
              {!project.deleted_at && (
                <button
                  className="button button--secondary"
                  type="button"
                  onClick={() =>
                    void run("status", () =>
                      operatorApiClient.setProjectStatus(project.id, !project.is_active),
                    )
                  }
                >
                  {project.is_active ? "Disable" : "Enable"}
                </button>
              )}
              {!project.deleted_at ? (
                <button
                  className="button button--danger"
                  type="button"
                  onClick={() => {
                    if (window.confirm("Archive this Project?"))
                      void run("archive", () => operatorApiClient.archiveProject(project.id));
                  }}
                >
                  Archive
                </button>
              ) : (
                <button
                  className="button button--primary"
                  type="button"
                  onClick={() =>
                    void run("restore", () => operatorApiClient.restoreProject(project.id))
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
            <h3>Ownership</h3>
            <StatusBadge status={project.ownership_locked ? "locked" : "migration required"} />
          </div>
          <div className="panel-body">
            <dl className="fact-grid">
              <div>
                <dt>Organization</dt>
                <dd>{owner?.name ?? shortId(project.organization_id)}</dd>
              </div>
              <div>
                <dt>Boundary</dt>
                <dd>{project.ownership_locked ? "Immutable" : "Legacy unlocked"}</dd>
              </div>
            </dl>
            {project.ownership_locked ? (
              <p className="muted-copy">
                Ownership is confirmed. General Project moves are not permitted.
              </p>
            ) : (
              <div className="stack-form">
                <label className="field-control">
                  <span>Target Organization</span>
                  <select
                    value={target}
                    onChange={(event) => {
                      setTarget(event.target.value);
                      setPreflight(null);
                    }}
                  >
                    {organizations
                      .filter((item) => item.is_active && !item.deleted_at)
                      .map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.name}
                        </option>
                      ))}
                  </select>
                </label>
                <label className="field-control">
                  <span>Audit reason</span>
                  <textarea
                    required
                    rows={3}
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                  />
                </label>
                <div className="button-row">
                  <button
                    className="button button--secondary"
                    type="button"
                    disabled={!reason || !target}
                    onClick={() => {
                      void (async () => {
                        setError("");
                        try {
                          setPreflight(
                            await operatorApiClient.getProjectOwnershipPreflight(
                              project.id,
                              target,
                            ),
                          );
                        } catch (caught) {
                          setError((caught as Error).message);
                        }
                      })();
                    }}
                  >
                    Dry-run preflight
                  </button>
                  <button
                    className="button button--primary"
                    type="button"
                    disabled={!reason || !preflight}
                    onClick={() =>
                      void run("reassign", () =>
                        target === project.organization_id
                          ? operatorApiClient.confirmProjectOwnership(
                              project.id,
                              project.organization_id,
                              reason,
                            )
                          : operatorApiClient.reassignProjectOwnership(
                              project.id,
                              project.organization_id,
                              target,
                              reason,
                            ),
                      )
                    }
                  >
                    {target === project.organization_id
                      ? "Confirm current owner"
                      : "Reassign and lock"}
                  </button>
                </div>
                {preflight && (
                  <div className="preflight-box">
                    <strong>
                      {preflight.can_reassign
                        ? "Ready to lock ownership"
                        : "Ownership already locked"}
                    </strong>
                    {Object.entries(preflight.resource_counts).map(([label, count]) => (
                      <span key={label}>
                        {label}: {count}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </section>
      </div>
    </>
  );
}

function ProjectConfig({ project }: { project: Project }) {
  const [effective, setEffective] = useState<EffectiveProjectAIConfig | null>(null);
  const [history, setHistory] = useState<ProjectAIConfigRevision[]>([]);
  const [capabilities, setCapabilities] = useState<ProviderCapability[]>([]);
  const [overrides, setOverrides] = useState<ProjectConfigOverrides>(inheritedProjectConfig);
  const [form, setForm] = useState<ProjectConfigForm>(emptyProjectConfigForm);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    const [config, revisions] = await Promise.all([
      operatorApiClient.getProjectAIConfig(project.id),
      operatorApiClient.getProjectAIConfigHistory(project.id),
    ]);
    const providerCapabilities = await operatorApiClient.getProviderCapabilities(
      config.configuration.llm.provider,
      config.configuration.llm.model,
    );
    const activeRevision = revisions.find((revision) => revision.id === config.active_revision_id);
    if (config.active_revision_id && !activeRevision) {
      throw new Error("The active AI configuration revision is unavailable. Reload and try again.");
    }
    setEffective(config);
    setHistory(revisions);
    setCapabilities(providerCapabilities);
    setOverrides(
      activeRevision
        ? configOverridesFromStored(activeRevision.configuration)
        : inheritedProjectConfig,
    );
    setForm(configFormFromEffective(config, activeRevision?.configuration ?? {}));
  }, [project.id]);
  useEffect(() => {
    setError("");
    void load().catch((caught: Error) => setError(caught.message));
  }, [load]);
  useEffect(() => {
    const model = form.model.trim();
    if (!model) return;
    const timer = window.setTimeout(() => {
      void operatorApiClient
        .getProviderCapabilities(form.provider, model)
        .then((items) => {
          setCapabilities(items);
          if (items[0]?.parameters.temperature.supported === false) {
            setOverrides((current) => ({ ...current, temperature: false }));
          }
        })
        .catch((caught: Error) => setError(caught.message));
    }, 200);
    return () => window.clearTimeout(timer);
  }, [form.model, form.provider]);
  if (!effective && !error) return <LoadingState label="Resolving Project AI configuration" />;
  if (error && !effective)
    return (
      <div className="failure-box" role="alert">
        {error}
      </div>
    );
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    const activeRevision = history.find(
      (revision) => revision.id === effective?.active_revision_id,
    );
    if (effective?.active_revision_id && !activeRevision) {
      setError("The active AI configuration revision is unavailable. Reload and try again.");
      setBusy(false);
      return;
    }
    const stored = activeRevision?.configuration ?? {};
    const selectedCapability = capabilities.find(
      (item) => item.provider === form.provider && item.model === form.model,
    );
    const configuration = buildSparseProjectConfig(stored, form, overrides, selectedCapability);
    if (!sparseHasOverrides(configuration) && !effective?.active_revision_id) {
      setError("All fields inherit deployment defaults, so no Project revision was created.");
      setBusy(false);
      return;
    }
    try {
      await operatorApiClient.createProjectAIConfig(
        project.id,
        configuration,
        effective?.active_revision_id ?? null,
        form.reason,
      );
      await load();
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const selectedCapability = capabilities.find(
    (item) => item.provider === form.provider && item.model === form.model,
  );
  const temperatureCapability = selectedCapability?.parameters.temperature;
  const tokenCapability = selectedCapability?.parameters.max_tokens;
  const baseline = inheritedFormFromEffective(effective);
  const inheritedClass = (overridden: boolean) =>
    overridden ? "field-control" : "field-control field-control--inherited";
  const changeField = <K extends ProjectConfigOverride>(key: K, value: ProjectConfigForm[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
    setOverrides((current) => ({ ...current, [key]: value !== baseline[key] }));
  };
  const toggleField = (key: ProjectConfigOverride, overridden: boolean) => {
    setOverrides((current) => ({ ...current, [key]: overridden }));
    if (!overridden) setForm((current) => ({ ...current, [key]: baseline[key] }));
  };
  const setOverride = (key: ProjectConfigOverride, enabled: boolean) => {
    setOverrides((current) => ({ ...current, [key]: enabled }));
  };
  const temperatureUnsupported = temperatureCapability?.supported === false;
  return (
    <>
      <section className="panel config-summary">
        <div>
          <p className="eyebrow">Active immutable policy</p>
          <h3>
            {effective?.active_revision_id
              ? `Revision ${shortId(effective.active_revision_id)}`
              : "Inherited deployment defaults"}
          </h3>
          <p className="muted-copy">
            Fingerprint {effective?.configuration_hash.slice(0, 12) ?? "—"}
            {" · "}Source policy configured{" "}
            {effective?.provenance.configured_source_policy_mode ?? "off"}
            {" · "}effective {effective?.provenance.effective_source_policy_mode ?? "off"}
            {" · "}deployment cap {effective?.provenance.source_policy_deployment_cap ?? "enforce"}
          </p>
        </div>
        <OriginSummary origins={effective?.origins ?? {}} />
      </section>
      {error && (
        <div className="failure-box" role="alert">
          {error}
        </div>
      )}
      <form className="panel progressive-form" onSubmit={(event) => void submit(event)}>
        <p className="muted-copy">
          Inherit global omits the field from the Project revision, so the saved policy uses the
          deployment default. Choosing another value turns Inherit off; turning it back on restores
          the default shown here.
        </p>
        <fieldset>
          <legend>
            <Settings2 size={17} /> Provider, model, translation, and rerank
          </legend>
          <ProjectAISettingsFields
            form={form}
            setForm={setForm}
            overrides={overrides}
            setOverride={setOverride}
            effective={effective}
          />
        </fieldset>
        <fieldset>
          <legend>Generation and chat defaults</legend>
          <div className="form-grid">
            <div className={inheritedClass(overrides.temperature)}>
              <FieldHint label="Temperature" text={PROJECT_AI_FIELD_HINTS.temperature} />
              <input
                aria-label="Temperature"
                type="number"
                step="0.1"
                min={temperatureCapability?.minimum ?? undefined}
                max={temperatureCapability?.maximum ?? undefined}
                value={form.temperature}
                disabled={temperatureUnsupported}
                onChange={(event) => changeField("temperature", event.target.value)}
              />
              <InheritanceToggle
                field="Temperature"
                overridden={overrides.temperature}
                disabled={temperatureUnsupported}
                onChange={(enabled) => toggleField("temperature", enabled)}
              />
              {temperatureUnsupported && (
                <small>This provider/model does not support temperature.</small>
              )}
            </div>
            <div className={inheritedClass(overrides.maxTokens)}>
              <FieldHint label="Maximum output tokens" text={PROJECT_AI_FIELD_HINTS.maxTokens} />
              <input
                aria-label="Maximum output tokens"
                type="number"
                min={tokenCapability?.minimum ?? 1}
                max={tokenCapability?.maximum ?? undefined}
                value={form.maxTokens}
                onChange={(event) => changeField("maxTokens", event.target.value)}
              />
              <InheritanceToggle
                field="Maximum output tokens"
                overridden={overrides.maxTokens}
                onChange={(enabled) => toggleField("maxTokens", enabled)}
              />
            </div>
          </div>
        </fieldset>
        <fieldset>
          <legend>Retrieval, citation, and evidence defaults</legend>
          <div className="form-grid">
            <div className={inheritedClass(overrides.strategy)}>
              <FieldHint label="Strategy" text={PROJECT_AI_FIELD_HINTS.strategy} />
              <select
                aria-label="Strategy"
                value={form.strategy}
                onChange={(event) => changeField("strategy", event.target.value)}
              >
                <option value="semantic">Semantic</option>
                <option value="hybrid">Hybrid</option>
              </select>
              <InheritanceToggle
                field="Strategy"
                overridden={overrides.strategy}
                onChange={(enabled) => toggleField("strategy", enabled)}
              />
            </div>
            <div className={inheritedClass(overrides.topK)}>
              <FieldHint label="Top K" text={PROJECT_AI_FIELD_HINTS.topK} />
              <input
                aria-label="Top K"
                type="number"
                min="1"
                value={form.topK}
                onChange={(event) => changeField("topK", event.target.value)}
              />
              <InheritanceToggle
                field="Top K"
                overridden={overrides.topK}
                onChange={(enabled) => toggleField("topK", enabled)}
              />
            </div>
            <div className={inheritedClass(overrides.evidence)}>
              <FieldHint label="Evidence threshold" text={PROJECT_AI_FIELD_HINTS.evidence} />
              <input
                aria-label="Evidence threshold"
                type="number"
                min="0"
                max="1"
                step="0.01"
                value={form.evidence}
                onChange={(event) => changeField("evidence", event.target.value)}
              />
              <InheritanceToggle
                field="Evidence threshold"
                overridden={overrides.evidence}
                onChange={(enabled) => toggleField("evidence", enabled)}
              />
            </div>
          </div>
        </fieldset>
        <fieldset>
          <legend>
            <ShieldCheck size={17} /> Advanced source policy
          </legend>
          <div className={inheritedClass(overrides.sourcePolicy)}>
            <FieldHint label="Rollout mode" text={PROJECT_AI_FIELD_HINTS.sourcePolicy} />
            <select
              aria-label="Source policy"
              value={form.sourcePolicy}
              onChange={(event) => changeField("sourcePolicy", event.target.value)}
            >
              <option value="off">Off — legacy-neutral</option>
              <option value="observe">Observe — diagnostics only</option>
              <option value="enforce">Enforce — filter and consolidate applicable sources</option>
            </select>
            <InheritanceToggle
              field="Source policy"
              overridden={overrides.sourcePolicy}
              onChange={(enabled) => toggleField("sourcePolicy", enabled)}
            />
          </div>
          <p className="muted-copy">
            The effective mode may be lowered by the deployment safety cap without changing this
            stored Project policy.
          </p>
        </fieldset>
        <div className="field-control">
          <FieldHint label="Revision reason" text={PROJECT_AI_FIELD_HINTS.reason} />
          <input
            required
            aria-label="Revision reason"
            value={form.reason}
            onChange={(event) => setForm({ ...form, reason: event.target.value })}
            placeholder="Why this policy changed"
          />
        </div>
        <p className="muted-copy">
          New conversations capture this policy. Existing conversation snapshots do not drift.
        </p>
        <button className="button button--primary" disabled={busy}>
          Create and activate revision
        </button>
      </form>
      <div className="detail-grid">
        <section className="panel">
          <div className="section-heading">
            <h3>Revision history</h3>
            <span>{history.length}</span>
          </div>
          <div className="record-list">
            {history.map((revision) => (
              <div className="record-row" key={revision.id}>
                <span>
                  <strong>Revision {revision.revision_number}</strong>
                  <small>
                    {revision.reason} · {formatDate(revision.created_at)}
                  </small>
                </span>
                {revision.id !== effective?.active_revision_id && (
                  <button
                    className="button button--secondary"
                    type="button"
                    onClick={() => {
                      void (async () => {
                        const reason = window.prompt("Restore reason");
                        if (!reason) return;
                        setBusy(true);
                        try {
                          await operatorApiClient.restoreProjectAIConfig(
                            project.id,
                            revision.id,
                            effective?.active_revision_id ?? null,
                            reason,
                          );
                          await load();
                        } finally {
                          setBusy(false);
                        }
                      })();
                    }}
                  >
                    Restore by copy
                  </button>
                )}
              </div>
            ))}
          </div>
        </section>
        <section className="panel">
          <div className="section-heading">
            <h3>Capability restrictions</h3>
            <span>{capabilities.length} providers</span>
          </div>
          <pre className="config-json">{JSON.stringify(capabilities, null, 2)}</pre>
        </section>
      </div>
    </>
  );
}

function ProjectSources({ project }: { project: Project }) {
  const documents = useDocuments(project.id);
  const sourceState = useSourceState(project.id);
  const upload = useUploadDocument(project.id);
  const [selectedDocumentId, setSelectedDocumentId] = useState("");
  const selectedDocument =
    documents.data?.items.find((item) => item.id === selectedDocumentId) ??
    documents.data?.items[0];
  const history = useSourceHistory(project.id, selectedDocument?.id ?? "");
  const activations = useSourceActivations(project.id, selectedDocument?.id ?? "");
  const current = sourceState.data?.items.find((item) => item.document_id === selectedDocument?.id);
  const [file, setFile] = useState<File | null>(null);
  const [uploadTitle, setUploadTitle] = useState("");
  const [uploadMode, setUploadMode] = useState<"independent" | "revision" | "modifies">(
    "independent",
  );
  const [uploadTarget, setUploadTarget] = useState("");
  const [form, setForm] = useState({
    title: "",
    label: "Revision",
    sourceType: "",
    lifecycle: "active",
    role: "primary",
    published: "",
    from: "",
    to: "",
    reason: "",
    newGroup: false,
    relationType: "",
    target: "",
  });
  const [error, setError] = useState("");
  useEffect(() => {
    if (selectedDocument) {
      setSelectedDocumentId(selectedDocument.id);
      setForm((value) => ({
        ...value,
        title: current?.revision.title ?? selectedDocument.filename,
        label: current ? `Revision ${current.revision.revision_number + 1}` : "Revision",
      }));
    }
  }, [selectedDocument, current]);
  const refresh = async () => {
    await Promise.all([
      documents.refetch(),
      sourceState.refetch(),
      history.refetch(),
      activations.refetch(),
    ]);
  };
  const uploadFile = async (event: FormEvent) => {
    event.preventDefault();
    if (!file) return;
    setError("");
    try {
      const target = sourceState.data?.items.find((item) => item.revision.id === uploadTarget);
      if (uploadMode !== "independent" && !target) {
        setError("Select the existing source revision this upload relates to.");
        return;
      }
      const metadata: SourceRevisionCreate | undefined =
        uploadMode === "independent" && !uploadTitle
          ? undefined
          : {
              activate: true,
              change_reason:
                uploadMode === "revision"
                  ? "Uploaded as a new revision of an existing source"
                  : uploadMode === "modifies"
                    ? "Uploaded as a modifying source"
                    : "Governed Operator upload",
              create_new_group: uploadMode !== "revision",
              ...(uploadMode === "revision" && target
                ? { source_group_id: target.revision.source_group_id }
                : {}),
              lifecycle_status: "active",
              revision_label:
                uploadMode === "revision" && target
                  ? `Revision ${target.revision.revision_number + 1}`
                  : "Initial",
              source_role: "primary",
              title: uploadTitle || file.name,
              relationships:
                uploadMode === "independent" || !target
                  ? []
                  : [
                      {
                        relationship_type: uploadMode === "revision" ? "replaces" : "modifies",
                        target_revision_id: target.revision.id,
                      },
                    ],
            };
      await upload.mutateAsync({ file, sourceMetadata: metadata });
      setFile(null);
      setUploadTitle("");
      setUploadMode("independent");
      setUploadTarget("");
      await refresh();
    } catch (caught) {
      setError((caught as Error).message);
    }
  };
  const createRevision = async (event: FormEvent) => {
    event.preventDefault();
    if (!selectedDocument) return;
    setError("");
    const revision: SourceRevisionCreate = {
      activate: true,
      change_reason: form.reason,
      create_new_group: form.newGroup,
      revision_label: form.label,
      title: form.title,
      source_type: form.sourceType || null,
      published_date: form.published || null,
      effective_from: form.from || null,
      effective_to: form.to || null,
      lifecycle_status: form.lifecycle as "unspecified" | "draft" | "active" | "retired",
      source_role: form.role as "unspecified" | "primary" | "supporting" | "reference",
      relationships:
        form.relationType && form.target
          ? [
              {
                relationship_type: form.relationType as "replaces" | "modifies",
                target_revision_id: form.target,
              },
            ]
          : [],
    };
    try {
      await operatorApiClient.createSourceRevision(project.id, selectedDocument.id, revision);
      setForm({ ...form, reason: "", relationType: "", target: "" });
      await refresh();
    } catch (caught) {
      setError((caught as Error).message);
    }
  };
  if (documents.isPending || sourceState.isPending) return <LoadingState label="Loading sources" />;
  return (
    <>
      <section className="panel source-generation">
        <div>
          <p className="eyebrow">Knowledge source state</p>
          <h3>Generation {sourceState.data?.generation ?? 0}</h3>
          <p>Metadata activation is independent of Document processing version and index builds.</p>
        </div>
        <FileClock size={22} />
      </section>
      {error && (
        <div className="failure-box" role="alert">
          {error}
        </div>
      )}
      <section className="panel">
        <div className="section-heading">
          <h3>Upload document and optional source metadata</h3>
          <span>Metadata is optional</span>
        </div>
        <form className="source-upload" onSubmit={(event) => void uploadFile(event)}>
          <label className="field-control">
            <span>File</span>
            <span className="file-picker">
              <input
                required
                type="file"
                aria-label="File"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
              <span className="file-picker__button">Choose file</span>
              <span className="file-picker__name">{file?.name ?? "No file chosen"}</span>
            </span>
          </label>
          <label className="field-control">
            <span>Source title (optional quick active defaults)</span>
            <input value={uploadTitle} onChange={(event) => setUploadTitle(event.target.value)} />
          </label>
          <label className="field-control">
            <span>Source treatment</span>
            <select
              value={uploadMode}
              onChange={(event) => {
                setUploadMode(event.target.value as typeof uploadMode);
                setUploadTarget("");
              }}
            >
              <option value="independent">New independent source</option>
              <option value="revision">New revision of an existing source</option>
              <option value="modifies">New source that modifies an existing source</option>
            </select>
          </label>
          {uploadMode !== "independent" && (
            <label className="field-control">
              <span>Existing source revision</span>
              <select
                required
                value={uploadTarget}
                onChange={(event) => setUploadTarget(event.target.value)}
              >
                <option value="">Select source</option>
                {sourceState.data?.items.map((item) => (
                  <option key={item.revision.id} value={item.revision.id}>
                    {item.revision.title} · r{item.revision.revision_number}
                  </option>
                ))}
              </select>
            </label>
          )}
          <p className="muted-copy">
            A new revision stays in the selected source group and replaces its target. A modifying
            source receives its own group and records a modifies relationship.
          </p>
          <div className="button-row">
            <button
              className="button button--primary"
              disabled={
                !file || upload.isPending || (uploadMode !== "independent" && !uploadTarget)
              }
            >
              Upload
            </button>
          </div>
        </form>
      </section>
      {!documents.data?.items.length ? (
        <EmptyState title="No documents" detail="Upload the first Project source." />
      ) : (
        <div className="source-workspace">
          <section className="panel">
            <div className="section-heading">
              <h3>Documents and active metadata</h3>
              <span>{documents.data.total}</span>
            </div>
            <div className="record-list">
              {documents.data.items.map((document) => {
                const source = sourceState.data?.items.find(
                  (item) => item.document_id === document.id,
                );
                return (
                  <button
                    className={
                      selectedDocument?.id === document.id
                        ? "record-row record-row--selected"
                        : "record-row"
                    }
                    type="button"
                    key={document.id}
                    onClick={() => setSelectedDocumentId(document.id)}
                  >
                    <span>
                      <strong>{source?.revision.title ?? document.filename}</strong>
                      <small>
                        {document.filename} · processing v{document.version} ·{" "}
                        {formatBytes(document.size_bytes)}
                      </small>
                    </span>
                    <span>
                      <StatusBadge status={source?.revision.lifecycle_status ?? "unspecified"} />
                      {source?.revision.warnings?.map((warning) => (
                        <small className="warning-text" key={warning}>
                          {warning.replaceAll("_", " ")}
                        </small>
                      ))}
                    </span>
                  </button>
                );
              })}
            </div>
          </section>
          {selectedDocument && (
            <section className="panel">
              <div className="section-heading">
                <div>
                  <h3>Create immutable source revision</h3>
                  <p>
                    {current
                      ? `Group ${shortId(current.revision.source_group_id)} · revision ${current.revision.revision_number}`
                      : "No active metadata"}
                  </p>
                </div>
                <StatusBadge status={current?.revision.source_role ?? "unspecified"} />
              </div>
              {current && (
                <div className="source-facts">
                  <span>
                    Type <strong>{current.revision.source_type || "unset"}</strong>
                  </span>
                  <span>
                    Lifecycle <strong>{current.revision.lifecycle_status}</strong>
                  </span>
                  <span>
                    Role <strong>{current.revision.source_role}</strong>
                  </span>
                </div>
              )}
              <form className="stack-form" onSubmit={(event) => void createRevision(event)}>
                <div className="form-grid">
                  <label className="field-control">
                    <span>Source title</span>
                    <input
                      required
                      value={form.title}
                      onChange={(event) => setForm({ ...form, title: event.target.value })}
                    />
                  </label>
                  <label className="field-control">
                    <span>Revision label</span>
                    <input
                      required
                      value={form.label}
                      onChange={(event) => setForm({ ...form, label: event.target.value })}
                    />
                  </label>
                  <label className="field-control">
                    <span>Source type</span>
                    <input
                      value={form.sourceType}
                      onChange={(event) => setForm({ ...form, sourceType: event.target.value })}
                    />
                  </label>
                  <label className="field-control">
                    <span>Lifecycle</span>
                    <select
                      value={form.lifecycle}
                      onChange={(event) => setForm({ ...form, lifecycle: event.target.value })}
                    >
                      {["unspecified", "draft", "active", "retired"].map((value) => (
                        <option key={value}>{value}</option>
                      ))}
                    </select>
                  </label>
                  <label className="field-control">
                    <span>Role</span>
                    <select
                      value={form.role}
                      onChange={(event) => setForm({ ...form, role: event.target.value })}
                    >
                      {["unspecified", "primary", "supporting", "reference"].map((value) => (
                        <option key={value}>{value}</option>
                      ))}
                    </select>
                  </label>
                  <label className="field-control">
                    <span>Published</span>
                    <input
                      type="date"
                      value={form.published}
                      onChange={(event) => setForm({ ...form, published: event.target.value })}
                    />
                  </label>
                  <label className="field-control">
                    <span>Effective from</span>
                    <input
                      type="date"
                      value={form.from}
                      onChange={(event) => setForm({ ...form, from: event.target.value })}
                    />
                  </label>
                  <label className="field-control">
                    <span>Effective to</span>
                    <input
                      type="date"
                      value={form.to}
                      onChange={(event) => setForm({ ...form, to: event.target.value })}
                    />
                  </label>
                </div>
                <label className="check-control">
                  <input
                    type="checkbox"
                    checked={form.newGroup}
                    onChange={(event) => setForm({ ...form, newGroup: event.target.checked })}
                  />{" "}
                  Create a separate source group (required for a modifying source)
                </label>
                <div className="form-grid">
                  <label className="field-control">
                    <span>Relationship</span>
                    <select
                      value={form.relationType}
                      onChange={(event) =>
                        setForm({
                          ...form,
                          relationType: event.target.value,
                          newGroup: event.target.value === "modifies",
                        })
                      }
                    >
                      <option value="">None</option>
                      <option value="replaces">Replaces</option>
                      <option value="modifies">Modifies</option>
                    </select>
                  </label>
                  <label className="field-control">
                    <span>Target revision</span>
                    <select
                      value={form.target}
                      onChange={(event) => setForm({ ...form, target: event.target.value })}
                    >
                      <option value="">Select target</option>
                      {sourceState.data?.items.map((item) => (
                        <option key={item.revision.id} value={item.revision.id}>
                          {item.revision.title} · r{item.revision.revision_number}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <label className="field-control">
                  <span>Change reason</span>
                  <textarea
                    required
                    value={form.reason}
                    onChange={(event) => setForm({ ...form, reason: event.target.value })}
                  />
                </label>
                <button className="button button--primary">Create and activate revision</button>
              </form>
            </section>
          )}
        </div>
      )}
      {selectedDocument && (
        <div className="detail-grid">
          <section className="panel">
            <div className="section-heading">
              <h3>Metadata history</h3>
              <span>{history.data?.length ?? 0} revisions</span>
            </div>
            <div className="record-list">
              {history.data?.map((revision) => (
                <div className="record-row" key={revision.id}>
                  <span>
                    <strong>
                      {revision.revision_label} · r{revision.revision_number}
                    </strong>
                    <small>
                      {revision.lifecycle_status} · {revision.source_role} ·{" "}
                      {formatDate(revision.created_at)}
                    </small>
                  </span>
                  {current?.revision.id !== revision.id && (
                    <button
                      className="button button--secondary"
                      type="button"
                      onClick={() => {
                        void (async () => {
                          const reason = window.prompt("Activation reason");
                          if (!reason) return;
                          await operatorApiClient.activateSourceRevision(
                            project.id,
                            revision.id,
                            reason,
                          );
                          await refresh();
                        })();
                      }}
                    >
                      Activate
                    </button>
                  )}
                </div>
              ))}
            </div>
          </section>
          <section className="panel">
            <div className="section-heading">
              <h3>Activation history</h3>
              <span>Current generation {sourceState.data?.current_generation ?? 0}</span>
            </div>
            <div className="record-list">
              {activations.data?.map((activation) => (
                <div className="record-row" key={activation.id}>
                  <span>
                    <strong>Generation {activation.generation}</strong>
                    <small>
                      {activation.reason} · {formatDate(activation.created_at)}
                    </small>
                  </span>
                  <small>{shortId(activation.source_revision_id)}</small>
                </div>
              ))}
            </div>
          </section>
        </div>
      )}
    </>
  );
}

function ProjectHistory({ projectId }: { projectId: string }) {
  const [events, setEvents] = useState<Awaited<
    ReturnType<typeof operatorApiClient.getProjectHistory>
  > | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setEvents(null);
    setError("");
    void operatorApiClient
      .getProjectHistory(projectId)
      .then(setEvents)
      .catch((caught: Error) => setError(caught.message));
  }, [projectId]);
  if (error)
    return (
      <div className="failure-box" role="alert">
        {error}
      </div>
    );
  if (!events) return <LoadingState label="Loading Project history" />;
  return (
    <section className="panel">
      <div className="section-heading">
        <h3>Administrative history</h3>
        <span>{events.length} events</span>
      </div>
      {events.length ? (
        <div className="record-list">
          {events.map((event) => (
            <div className="record-row" key={event.id}>
              <span>
                <strong>{event.event_type}</strong>
                <small>
                  {event.actor_type} · {formatDate(event.created_at)}
                </small>
              </span>
              <small>{event.outcome}</small>
            </div>
          ))}
        </div>
      ) : (
        <EmptyState title="No history" detail="Administrative events will appear here." />
      )}
    </section>
  );
}

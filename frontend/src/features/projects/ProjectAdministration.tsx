import { Database, FileClock, Plus, Settings2, X } from "lucide-react";
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
  type SourceRevision,
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
  ProjectAISettingsFields,
  buildSparseProjectConfig,
  configOverridesFromStored,
  configFormFromEffective,
  configFormFromDeployment,
  emptyProjectConfigForm,
  inheritedProjectConfig,
  sparseHasOverrides,
  type ProjectConfigForm,
  type ProjectConfigOverride,
  type ProjectConfigOverrides,
} from "./ProjectAISettingsFields";
import {
  buildSourceUploadMetadata,
  buildSourceMetadataCorrection,
  hasInvalidEffectiveInterval,
  type SourceMetadataDraft,
  type SourceCorrectionTreatment,
  type SourceUploadMode,
} from "../sources/sourceUploadMetadata";

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

type SourceRevisionForm = {
  title: string;
  label: string;
  sourceType: string;
  lifecycle: "unspecified" | "draft" | "active" | "retired";
  role: "unspecified" | "primary" | "supporting" | "reference";
  published: string;
  from: string;
  to: string;
  reason: string;
  treatment: SourceCorrectionTreatment;
  target: string;
};

function dateInputValue(value: string | null | undefined) {
  return value?.slice(0, 10) ?? "";
}

function formatSourceDate(value: string | null | undefined) {
  if (!value) return "Not set";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: "UTC" }).format(
    new Date(`${dateInputValue(value)}T00:00:00Z`),
  );
}

function sourceRevisionForm(
  revision: SourceRevision | undefined,
  fallbackTitle: string,
): SourceRevisionForm {
  return {
    title: revision?.title ?? fallbackTitle,
    label: revision ? `Revision ${revision.revision_number + 1}` : "Revision 1",
    sourceType: revision?.source_type ?? "",
    lifecycle: revision?.lifecycle_status ?? "active",
    role: revision?.source_role ?? "primary",
    published: dateInputValue(revision?.published_date),
    from: dateInputValue(revision?.effective_from),
    to: dateInputValue(revision?.effective_to),
    reason: "",
    treatment: "keep",
    target: "",
  };
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
        responseMode: next.responseMode,
      };
      setOverrides((flags) => ({
        ...flags,
        responseMode: merged.responseMode !== next.responseMode,
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
      const sparseConfiguration = buildSparseProjectConfig({}, form, overrides);
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
              Leave Inherit global on to use this deployment's approved profile, generation model,
              and response defaults. No AI revision is created if everything stays inherited.
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
  const [overrides, setOverrides] = useState<ProjectConfigOverrides>(inheritedProjectConfig);
  const [form, setForm] = useState<ProjectConfigForm>(emptyProjectConfigForm);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    const [config, revisions] = await Promise.all([
      operatorApiClient.getProjectAIConfig(project.id),
      operatorApiClient.getProjectAIConfigHistory(project.id),
    ]);
    const activeRevision = revisions.find((revision) => revision.id === config.active_revision_id);
    if (config.active_revision_id && !activeRevision) {
      throw new Error("The active AI configuration revision is unavailable. Reload and try again.");
    }
    const stored = activeRevision?.configuration ?? {};
    setEffective(config);
    setHistory(revisions);
    setOverrides(configOverridesFromStored(stored));
    setForm(configFormFromEffective(config, stored));
  }, [project.id]);
  useEffect(() => {
    setError("");
    void load().catch((caught: Error) => setError(caught.message));
  }, [load]);
  if (!effective && !error) return <LoadingState label="Resolving Project AI configuration" />;
  if (error && !effective) {
    return (
      <div className="failure-box" role="alert">
        {error}
      </div>
    );
  }

  const activeRevision = history.find((revision) => revision.id === effective?.active_revision_id);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const stored = activeRevision?.configuration ?? {};
      const configuration = buildSparseProjectConfig(stored, form, overrides);
      if (!sparseHasOverrides(configuration) && !effective?.active_revision_id) {
        throw new Error(
          "All fields inherit deployment defaults, so no Project revision was created.",
        );
      }
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
  const normalizeProfile = async () => {
    if (!activeRevision || activeRevision.schema_version !== 2) return;
    setBusy(true);
    setError("");
    try {
      const preview = await operatorApiClient.previewProjectAIProfileNormalization(project.id);
      const label = preview.result.base_profile_id ?? "Custom";
      const reason = window.prompt(
        `Normalize this append-only V2 revision to ${label}. Effective behavior and index identity remain unchanged. Enter an audit reason.`,
        "Normalize V2 execution profile identity",
      );
      if (!reason?.trim()) return;
      await operatorApiClient.normalizeProjectAIProfile(project.id, activeRevision.id, reason);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to normalize profile identity.");
    } finally {
      setBusy(false);
    }
  };
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
            Effective hash {effective?.effective_value_hash?.slice(0, 12) ?? "—"}
            {" · "}resolution {effective?.resolution_fingerprint?.slice(0, 12) ?? "—"}
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
          Project behavior is sparse. RAG execution is an inherit/preset selection or a complete
          Custom bundle. Provider, web budget, calibration, citation, and source-governance controls
          are deployment or code owned.
        </p>
        <fieldset>
          <legend>
            <Settings2 size={17} /> Project behavior and canonical execution
          </legend>
          <ProjectAISettingsFields
            form={form}
            setForm={setForm}
            overrides={overrides}
            setOverride={(key, enabled) =>
              setOverrides((current) => ({ ...current, [key]: enabled }))
            }
            effective={effective}
            deploymentConfiguration={effective?.deployment_configuration}
            allowedGenerationModels={effective?.allowed_generation_models}
            ragProfiles={effective?.rag_profiles}
          />
        </fieldset>
        <div className="field-control">
          <label htmlFor="project-ai-reason">Revision reason</label>
          <input
            id="project-ai-reason"
            required
            aria-label="Revision reason"
            value={form.reason}
            onChange={(event) => setForm((current) => ({ ...current, reason: event.target.value }))}
            placeholder="Why this policy changed"
          />
        </div>
        <p className="muted-copy">
          New conversations capture this policy. Existing snapshots do not drift.
        </p>
        <button className="button button--primary" disabled={busy}>
          Create and activate revision
        </button>
        {activeRevision?.schema_version === 2 && (
          <button
            className="button button--secondary"
            type="button"
            disabled={busy}
            onClick={() => void normalizeProfile()}
          >
            Normalize profile identity
          </button>
        )}
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
                  <strong>
                    Revision {revision.revision_number} · V{revision.schema_version}
                  </strong>
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
                        } catch (caught) {
                          setError((caught as Error).message);
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
            <h3>Approved generation models</h3>
            <span>{effective?.allowed_generation_models?.length ?? 0}</span>
          </div>
          <p className="muted-copy">
            {effective?.allowed_generation_models
              ?.map((model) => `${model.id} (${model.provider}/${model.model})`)
              .join(", ") || "Deployment default only"}
          </p>
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
  const [uploadMode, setUploadMode] = useState<SourceUploadMode>("independent");
  const [uploadTarget, setUploadTarget] = useState("");
  const [uploadSourceType, setUploadSourceType] = useState("");
  const [uploadLifecycle, setUploadLifecycle] =
    useState<SourceMetadataDraft["lifecycle"]>("active");
  const [uploadSourceRole, setUploadSourceRole] = useState<SourceMetadataDraft["role"]>("primary");
  const [uploadPublished, setUploadPublished] = useState("");
  const [uploadEffectiveFrom, setUploadEffectiveFrom] = useState("");
  const [uploadEffectiveTo, setUploadEffectiveTo] = useState("");
  const [uploadChangeReason, setUploadChangeReason] = useState("");
  const [form, setForm] = useState<SourceRevisionForm>(() => sourceRevisionForm(undefined, ""));
  const relationshipTargets = sourceState.data?.items.filter(
    (item) => item.revision.id !== current?.revision.id,
  );
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [creatingRevision, setCreatingRevision] = useState(false);
  useEffect(() => {
    if (selectedDocument) {
      setSelectedDocumentId((value) =>
        value === selectedDocument.id ? value : selectedDocument.id,
      );
      setForm(sourceRevisionForm(current?.revision, selectedDocument.filename));
    }
  }, [current?.revision, selectedDocument]);
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
      const draft: SourceMetadataDraft = {
        title: uploadTitle,
        sourceType: uploadSourceType,
        lifecycle: uploadLifecycle,
        role: uploadSourceRole,
        publishedDate: uploadPublished,
        effectiveFrom: uploadEffectiveFrom,
        effectiveTo: uploadEffectiveTo,
        changeReason: uploadChangeReason,
      };
      if (hasInvalidEffectiveInterval(draft)) {
        setError("Effective to must be on or after effective from.");
        return;
      }
      const metadata = buildSourceUploadMetadata({
        filename: file.name,
        mode: uploadMode,
        target: target?.revision,
        draft,
        defaultReason: "Governed Operator upload",
      });
      await upload.mutateAsync({ file, sourceMetadata: metadata });
      setFile(null);
      setUploadTitle("");
      setUploadMode("independent");
      setUploadTarget("");
      setUploadSourceType("");
      setUploadLifecycle("active");
      setUploadSourceRole("primary");
      setUploadPublished("");
      setUploadEffectiveFrom("");
      setUploadEffectiveTo("");
      setUploadChangeReason("");
      await refresh();
    } catch (caught) {
      setError((caught as Error).message);
    }
  };
  const createRevision = async (event: FormEvent) => {
    event.preventDefault();
    if (!selectedDocument) return;
    setError("");
    setNotice("");
    const changeReason = form.reason.trim();
    const title = form.title.trim();
    const label = form.label.trim();
    const sourceType = form.sourceType.trim();
    if (!title || !label) {
      setError("Source title and revision label are required.");
      return;
    }
    if (form.from && form.to && form.to < form.from) {
      setError("Effective to must be on or after effective from.");
      return;
    }
    const target = sourceState.data?.items.find(
      (item) => item.revision.id === form.target,
    )?.revision;
    if ((form.treatment === "revision" || form.treatment === "modifies") && !target) {
      setError("Select the existing source this correction relates to.");
      return;
    }
    if (!current) return;
    const revision = buildSourceMetadataCorrection({
      current: current.revision,
      treatment: form.treatment,
      target,
      draft: {
        title,
        sourceType,
        lifecycle: form.lifecycle,
        role: form.role,
        publishedDate: form.published,
        effectiveFrom: form.from,
        effectiveTo: form.to,
        changeReason,
      },
    });
    if (!revision) return;
    revision.revision_label = label;
    setCreatingRevision(true);
    try {
      const created = await operatorApiClient.createSourceRevision(
        project.id,
        selectedDocument.id,
        revision,
      );
      setForm(sourceRevisionForm(created.revision, selectedDocument.filename));
      setNotice(
        `Revision ${created.revision.revision_number} is active. Its validity dates are saved with this immutable revision.`,
      );
      await refresh();
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setCreatingRevision(false);
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
      {notice && (
        <div className="notice-card" role="status">
          {notice}
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
                setUploadMode(event.target.value as SourceUploadMode);
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
          <label className="field-control">
            <span>Source role</span>
            <select
              value={uploadSourceRole}
              onChange={(event) =>
                setUploadSourceRole(event.target.value as SourceMetadataDraft["role"])
              }
            >
              <option value="primary">Primary</option>
              <option value="supporting">Supporting</option>
              <option value="reference">Reference</option>
              <option value="unspecified">Unspecified</option>
            </select>
          </label>
          <label className="field-control">
            <span>Lifecycle</span>
            <select
              value={uploadLifecycle}
              onChange={(event) =>
                setUploadLifecycle(event.target.value as SourceMetadataDraft["lifecycle"])
              }
            >
              <option value="active">Active</option>
              <option value="draft">Draft</option>
              <option value="retired">Retired</option>
              <option value="unspecified">Unspecified</option>
            </select>
          </label>
          <label className="field-control">
            <span>Source type</span>
            <input
              value={uploadSourceType}
              onChange={(event) => setUploadSourceType(event.target.value)}
            />
          </label>
          <label className="field-control">
            <span>Published</span>
            <input
              type="date"
              value={uploadPublished}
              onChange={(event) => setUploadPublished(event.target.value)}
            />
          </label>
          <label className="field-control">
            <span>Effective from</span>
            <input
              type="date"
              value={uploadEffectiveFrom}
              max={uploadEffectiveTo || undefined}
              onChange={(event) => setUploadEffectiveFrom(event.target.value)}
            />
          </label>
          <label className="field-control">
            <span>Effective to</span>
            <input
              type="date"
              value={uploadEffectiveTo}
              min={uploadEffectiveFrom || undefined}
              onChange={(event) => setUploadEffectiveTo(event.target.value)}
            />
          </label>
          <label className="field-control source-upload__reason">
            <span>Change reason</span>
            <input
              value={uploadChangeReason}
              placeholder="Optional — records why this source was added"
              onChange={(event) => setUploadChangeReason(event.target.value)}
            />
          </label>
          <p className="muted-copy">
            Source metadata is optional: defaults create a neutral initial record. Entering any
            metadata saves it with this upload. A new revision stays in the selected source group; a
            modifying source receives its own group.
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
                    onClick={() => {
                      setSelectedDocumentId(document.id);
                      setNotice("");
                    }}
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
                  <h3>Correct source metadata</h3>
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
                  <span>
                    Published <strong>{formatSourceDate(current.revision.published_date)}</strong>
                  </span>
                  <span>
                    Effective from{" "}
                    <strong>{formatSourceDate(current.revision.effective_from)}</strong>
                  </span>
                  <span>
                    Effective to <strong>{formatSourceDate(current.revision.effective_to)}</strong>
                  </span>
                </div>
              )}
              <form
                className="stack-form source-revision-form"
                onSubmit={(event) => void createRevision(event)}
              >
                <div className="source-revision-form__section">
                  <div className="source-revision-form__section-heading">
                    <div>
                      <h4>Source details</h4>
                      <p>Describe the document and the status it should have in this Project.</p>
                    </div>
                    <span>Revision {current ? current.revision.revision_number + 1 : 1}</span>
                  </div>
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
                        onChange={(event) =>
                          setForm({
                            ...form,
                            lifecycle: event.target.value as SourceRevisionForm["lifecycle"],
                          })
                        }
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
                        onChange={(event) =>
                          setForm({
                            ...form,
                            role: event.target.value as SourceRevisionForm["role"],
                          })
                        }
                      >
                        {["unspecified", "primary", "supporting", "reference"].map((value) => (
                          <option key={value}>{value}</option>
                        ))}
                      </select>
                    </label>
                  </div>
                </div>
                <div className="source-revision-form__section">
                  <div className="source-revision-form__section-heading">
                    <div>
                      <h4>Validity</h4>
                      <p>
                        These dates are preserved on this revision and apply to retrieval only when
                        source policy is enforced.
                      </p>
                    </div>
                  </div>
                  <div className="form-grid source-revision-form__dates">
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
                        max={form.to || undefined}
                        onChange={(event) => setForm({ ...form, from: event.target.value })}
                      />
                    </label>
                    <label className="field-control">
                      <span>Effective to</span>
                      <input
                        type="date"
                        value={form.to}
                        min={form.from || undefined}
                        onChange={(event) => setForm({ ...form, to: event.target.value })}
                      />
                    </label>
                  </div>
                </div>
                <div className="source-revision-form__section source-revision-form__relationship">
                  <div className="source-revision-form__section-heading">
                    <div>
                      <h4>Source treatment</h4>
                      <p>
                        Correct an accidental upload choice without changing the document or its
                        processing results.
                      </p>
                    </div>
                  </div>
                  <div className="form-grid">
                    <label className="field-control">
                      <span>Correct treatment</span>
                      <select
                        aria-label="Correct treatment"
                        value={form.treatment}
                        onChange={(event) =>
                          setForm({
                            ...form,
                            treatment: event.target.value as SourceCorrectionTreatment,
                            target: "",
                            label:
                              event.target.value === "independent" ||
                              event.target.value === "modifies"
                                ? "Revision 1"
                                : event.target.value === "keep"
                                  ? `Revision ${(current?.revision.revision_number ?? 0) + 1}`
                                  : form.label,
                          })
                        }
                      >
                        <option value="keep">Keep this source's current treatment</option>
                        <option value="independent">New independent source</option>
                        <option value="revision">Latest revision of an existing source</option>
                        <option value="modifies">Modifies an existing source</option>
                      </select>
                    </label>
                    {(form.treatment === "revision" || form.treatment === "modifies") && (
                      <label className="field-control">
                        <span>Existing source</span>
                        <select
                          aria-label="Correction target source"
                          required
                          value={form.target}
                          onChange={(event) => {
                            const target = relationshipTargets?.find(
                              (item) => item.revision.id === event.target.value,
                            )?.revision;
                            setForm({
                              ...form,
                              target: event.target.value,
                              label:
                                form.treatment === "revision" && target
                                  ? `Revision ${target.revision_number + 1}`
                                  : form.label,
                            });
                          }}
                        >
                          <option value="">Select source</option>
                          {relationshipTargets?.map((item) => (
                            <option key={item.revision.id} value={item.revision.id}>
                              {item.revision.title} · r{item.revision.revision_number}
                            </option>
                          ))}
                        </select>
                      </label>
                    )}
                  </div>
                  <p className="muted-copy">
                    “Latest revision” joins the selected source’s history and replaces it.
                    “Modifies” stays a separate source and records the link. “Independent” removes
                    any active relationship from this document.
                  </p>
                </div>
                <label className="field-control">
                  <span>Change reason</span>
                  <small className="field-description">
                    Optional. Records why this immutable revision is being activated.
                  </small>
                  <textarea
                    rows={3}
                    placeholder="Optional — defaults to “Source metadata created”"
                    value={form.reason}
                    onChange={(event) => setForm({ ...form, reason: event.target.value })}
                  />
                </label>
                <div className="source-revision-form__actions">
                  <button className="button button--primary" disabled={creatingRevision}>
                    {creatingRevision ? "Saving correction…" : "Save metadata correction"}
                  </button>
                  <span>
                    Saved as a new immutable metadata revision. No upload, reprocessing, or index
                    rebuild is needed.
                  </span>
                </div>
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
                    <small>
                      Published {formatSourceDate(revision.published_date)} · effective{" "}
                      {formatSourceDate(revision.effective_from)} to{" "}
                      {formatSourceDate(revision.effective_to)}
                    </small>
                    <small>{revision.change_reason}</small>
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

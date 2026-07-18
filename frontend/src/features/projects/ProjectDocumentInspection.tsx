import { useEffect, useMemo, useState } from "react";
import { useDocuments, useProjects } from "../../api/operatorConsoleQueries";
import { EmptyState, ErrorState, LoadingState } from "../../components/QueryStatePanel";
import { ProjectSelector } from "../../components/ProjectSelector";
import { StatusBadge } from "../../components/StatusBadge";
import { formatBytes, formatDate } from "../../shared/formatters";
import { DocumentLifecycleDetails } from "./DocumentLifecycleDetails";
import { CorpusLifecycleActions } from "./CorpusLifecycleActions";

export function ProjectDocumentInspection() {
  const projects = useProjects();
  const [projectId, setProjectId] = useState("");
  const [selectedDocument, setSelectedDocument] = useState("");
  const [search, setSearch] = useState("");
  useEffect(() => {
    if (!projectId && projects.data?.items[0]) setProjectId(projects.data.items[0].id);
  }, [projectId, projects.data]);
  const documents = useDocuments(projectId);
  const visible = useMemo(
    () =>
      (documents.data?.items ?? []).filter((document) =>
        document.filename.toLowerCase().includes(search.toLowerCase()),
      ),
    [documents.data, search],
  );
  const selected = documents.data?.items.find((document) => document.id === selectedDocument);

  if (projects.isPending) return <LoadingState label="Loading projects" />;
  if (projects.isError)
    return <ErrorState error={projects.error} retry={() => void projects.refetch()} />;
  if (!projects.data.items.length)
    return (
      <EmptyState
        title="No projects yet"
        detail="Project and document inspection becomes available after the first project is created."
      />
    );

  return (
    <>
      <CorpusLifecycleActions projectId={projectId} />
      <div className={`workspace-grid ${selected ? "workspace-grid--detail" : ""}`}>
        <section className="panel workspace-grid__main">
          <div className="project-summary">
            <ProjectSelector
              projects={projects.data.items}
              value={projectId}
              onChange={(value) => {
                setProjectId(value);
                setSelectedDocument("");
              }}
            />
            <label className="field-control field-control--grow">
              <span>Search documents</span>
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                type="search"
                placeholder="Filename"
              />
            </label>
            <div>
              <span>Total documents</span>
              <strong>{documents.data?.total ?? 0}</strong>
            </div>
          </div>
          {documents.isPending ? (
            <LoadingState label="Loading documents" />
          ) : documents.isError ? (
            <ErrorState error={documents.error} retry={() => void documents.refetch()} />
          ) : visible.length === 0 ? (
            <EmptyState
              title={search ? "No matching documents" : "No documents"}
              detail={
                search ? "Try a different filename." : "This project has no uploaded documents yet."
              }
            />
          ) : (
            <div className="table-scroll">
              <table>
                <caption className="sr-only">Project documents</caption>
                <thead>
                  <tr>
                    <th>Document</th>
                    <th>Status</th>
                    <th>Version</th>
                    <th>Parser</th>
                    <th>Quality</th>
                    <th>Size</th>
                    <th>Updated</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((document) => (
                    <tr
                      key={document.id}
                      className={selectedDocument === document.id ? "row--selected" : ""}
                    >
                      <td>
                        <button
                          className="table-link table-link--wide"
                          type="button"
                          onClick={() => setSelectedDocument(document.id)}
                        >
                          {document.filename}
                        </button>
                      </td>
                      <td>
                        <StatusBadge status={document.status} />
                      </td>
                      <td>v{document.version}</td>
                      <td>{document.accepted_parser ?? document.parser_name ?? "—"}</td>
                      <td>
                        {document.parse_quality_score === null ||
                        document.parse_quality_score === undefined
                          ? "—"
                          : `${Math.round(document.parse_quality_score * 100)}%`}
                      </td>
                      <td>{formatBytes(document.size_bytes)}</td>
                      <td>{formatDate(document.updated_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
        {selected && (
          <DocumentLifecycleDetails
            projectId={projectId}
            document={selected}
            onClose={() => setSelectedDocument("")}
          />
        )}
      </div>
    </>
  );
}

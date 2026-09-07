import { useState } from "react";
import { useQueries } from "@tanstack/react-query";
import { operatorApiClient, type SourceRevision } from "../../api/operatorApiClient";
import type { SourceModification } from "./sourceUploadMetadata";

/** One shared, explicit multi-target control for upload and metadata corrections. */
export function SourceModificationPicker({
  projectId,
  sources,
  value,
  onChange,
}: {
  projectId: string;
  sources: SourceRevision[];
  value: SourceModification[];
  onChange: (value: SourceModification[]) => void;
}) {
  const [search, setSearch] = useState("");
  const known = new Set(sources.map((source) => source.id));
  const historical = useQueries({
    queries: value
      .filter((item) => !known.has(item.target_revision_id))
      .map((item) => ({
        queryKey: ["source-revision", projectId, item.target_revision_id],
        queryFn: () => operatorApiClient.getSourceRevision(projectId, item.target_revision_id),
        staleTime: Infinity,
        retry: false,
      })),
  });
  const resolvedSources = [
    ...sources,
    ...historical.flatMap((query) => (query.data ? [query.data] : [])),
  ];
  return (
    <fieldset className="source-modifications">
      <legend>Sources this document modifies ({value.length} selected)</legend>
      <p className="muted-copy">
        Select every affected source, including each stored language edition. Base texts remain
        available; the applicable provisions and dates determine what changes.
      </p>
      <label className="field-control">
        <span>Find sources to modify</span>
        <input type="search" value={search} onChange={(event) => setSearch(event.target.value)} />
      </label>
      <div className="source-modifications__options">
        {sources
          .filter((source) => source.title.toLowerCase().includes(search.toLowerCase()))
          .map((source) => (
            <label className="source-modifications__option" key={source.id}>
              <input
                type="checkbox"
                disabled={value.some(
                  (item) =>
                    item.target_revision_id !== source.id &&
                    resolvedSources.find((candidate) => candidate.id === item.target_revision_id)
                      ?.source_group_id === source.source_group_id,
                )}
                checked={value.some((item) => item.target_revision_id === source.id)}
                onChange={(event) =>
                  onChange(
                    event.target.checked
                      ? [...value, { target_revision_id: source.id, target_provisions: [] }]
                      : value.filter((item) => item.target_revision_id !== source.id),
                  )
                }
              />
              <span>
                {source.title} · r{source.revision_number} · {source.lifecycle_status}
              </span>
            </label>
          ))}
      </div>
      {value.map((item) => {
        const source = resolvedSources.find(
          (candidate) => candidate.id === item.target_revision_id,
        );
        return (
          <div className="source-modifications__selected" key={item.target_revision_id}>
            <span>{source?.title ?? `Saved revision ${item.target_revision_id}`}</span>
            {!known.has(item.target_revision_id) && (
              <small>
                Saved historical revision. Review this link before choosing another revision of the
                same source.
              </small>
            )}
            <button
              type="button"
              className="button button--secondary"
              aria-label={`Remove modification of ${source?.title ?? item.target_revision_id}`}
              onClick={() => onChange(value.filter((candidate) => candidate !== item))}
            >
              Remove
            </button>
            {item.target_provisions.length > 0 && (
              <small>Saved provision scope: {item.target_provisions.join("; ")}</small>
            )}
          </div>
        );
      })}
      {!sources.length && <p className="muted-copy">No other sources are available.</p>}
      <small>
        Unscoped links require the answer to establish the affected rules from the documents.
      </small>
    </fieldset>
  );
}

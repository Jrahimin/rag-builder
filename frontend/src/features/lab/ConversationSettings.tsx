import { useEffect, useState } from "react";
import { operatorApiClient, type EffectiveProjectAIConfig } from "../../api/operatorApiClient";

export function ConversationSettings({
  projectId,
  snapshot,
}: {
  projectId: string;
  snapshot: Record<string, unknown>;
}) {
  const [current, setCurrent] = useState<EffectiveProjectAIConfig | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    setCurrent(null);
    setFailed(false);
    void operatorApiClient.getProjectAIConfig(projectId).then(
      (value) => {
        if (active) setCurrent(value);
      },
      () => {
        if (active) setFailed(true);
      },
    );
    return () => {
      active = false;
    };
  }, [projectId, snapshot.config_snapshot_id]);
  const label = (value: unknown) =>
    typeof value === "string" ? value.replaceAll("_", " ") : "Unavailable";
  const enabled = (value: unknown) =>
    value === true ? "On" : value === false ? "Off" : "Unavailable";
  const currentRevision = current?.provenance.project_config_revision_number;
  const stale = current != null && snapshot.project_revision !== currentRevision;
  return (
    <section className="notice-card" aria-label="Conversation configuration">
      <strong>Conversation and current Project settings</strong>
      <table>
        <thead>
          <tr>
            <th>Setting</th>
            <th>This conversation</th>
            <th>Project now</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <th>Revision</th>
            <td>
              {typeof snapshot.project_revision === "number" ? snapshot.project_revision : "Legacy"}
            </td>
            <td>{current ? String(currentRevision ?? "Deployment default") : "Unavailable"}</td>
          </tr>
          <tr>
            <th>Evidence approach</th>
            <td>{label(snapshot.evidence_approach)}</td>
            <td>{label(current?.configuration.evidence_approach)}</td>
          </tr>
          <tr>
            <th>Query translation</th>
            <td>{enabled(snapshot.translation_enabled)}</td>
            <td>{enabled(current?.configuration.retrieval.query_translation_enabled)}</td>
          </tr>
        </tbody>
      </table>
      <p>
        {stale
          ? "This conversation retains an older configuration snapshot. "
          : "This answer uses its saved configuration snapshot. "}
        Project changes apply to a new conversation or an explicit snapshot update.
      </p>
      {failed && (
        <p>
          Current Project settings could not be loaded. The saved settings above still describe this
          answer.
        </p>
      )}
    </section>
  );
}

import type { Project } from "../api/operatorApiClient";

export function ProjectSelector({
  projects,
  value,
  onChange,
  includeAll = false,
}: {
  projects: Project[];
  value: string;
  onChange: (projectId: string) => void;
  includeAll?: boolean;
}) {
  return (
    <label className="field-control">
      <span>Project</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {includeAll && <option value="">All projects</option>}
        {projects.map((project) => (
          <option key={project.id} value={project.id}>
            {project.name}
          </option>
        ))}
      </select>
    </label>
  );
}

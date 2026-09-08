export function EvidenceSummary({ summary }: { summary: Record<string, unknown> }) {
  const value = (key: string) => {
    const item = summary[key];
    return typeof item === "number" || typeof item === "string"
      ? String(item).replaceAll("_", " ")
      : "Unavailable";
  };
  return (
    <section className="notice-card" aria-label="Evidence coverage">
      <strong>Evidence coverage and claim verification</strong>
      <p>
        Coverage describes the evidence reviewed. Claim verification checks the generated answer.
        Reviewed works do not imply independent authorship.
      </p>
      <table>
        <tbody>
          {(
            [
              ["candidates", "Initial candidates"],
              ["admitted_passages", "Admitted passages"],
              ["context_passages", "Passages used for this answer"],
              ["cited_documents", "Documents cited in the answer"],
              ["reviewed_works", "Distinct works reviewed"],
              ["coverage", "Evidence coverage"],
              ["claim_verification", "Claim verification"],
            ] as const
          ).map(([key, label]) => (
            <tr key={key}>
              <th scope="row">{label}</th>
              <td>{value(key)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <details>
        <summary>Coverage method and input provenance</summary>
        <pre className="json-view">{JSON.stringify(summary, null, 2)}</pre>
      </details>
    </section>
  );
}

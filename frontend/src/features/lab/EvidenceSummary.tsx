export function EvidenceSummary({ summary }: { summary: Record<string, unknown> }) {
  const value = (key: string) => {
    const item = summary[key];
    return typeof item === "number" || typeof item === "string"
      ? String(item).replaceAll("_", " ")
      : "Unavailable";
  };
  const rows: ReadonlyArray<readonly [string, string]> = [
    ["candidates", "Initial candidates"],
    ["admitted_passages", "Newly admitted passages"],
    ...(typeof summary.reused_cited_passages === "number"
      ? ([["reused_cited_passages", "Reused cited passages"]] as const)
      : []),
    ["context_passages", "Passages supplied to generation"],
    ["cited_passages", "Passages cited in the answer"],
    ["cited_documents", "Documents cited in the answer"],
    ["reviewed_works", "Distinct works reviewed"],
    ["coverage", "Evidence coverage"],
    ["claim_verification", "Claim verification"],
    ["factual_claims", "Factual claims checked"],
    ["supported_factual_claims", "Factual claims supported"],
    ["unverified_factual_claims", "Factual claims unverified"],
    ["unsupported_factual_claims", "Factual claims unsupported"],
    ["coverage_scope_claims", "Coverage statements checked"],
  ];
  return (
    <section className="notice-card" aria-label="Evidence coverage">
      <strong>Evidence coverage and claim verification</strong>
      <p>
        Coverage describes the evidence reviewed. Claim verification checks the generated answer.
        Reviewed works do not imply independent authorship.
      </p>
      <table>
        <tbody>
          {rows.map(([key, label]) => (
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

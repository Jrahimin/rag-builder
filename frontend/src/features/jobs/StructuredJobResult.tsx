function humanize(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function displayValue(value: unknown) {
  if (typeof value === "string") return value;
  if (typeof value === "number") return value.toLocaleString();
  if (typeof value === "bigint") return value.toString();
  if (typeof value === "undefined") return "—";
  if (typeof value === "symbol") return value.description ?? "Symbol";
  if (typeof value === "function") return value.name || "Function";
  return "—";
}

export function StructuredJobResult({ result }: { result: Record<string, unknown> }) {
  const reconciliationKeys = ["expected", "actual", "missing", "orphan", "consistent"];
  const isReconciliation = reconciliationKeys.some((key) => key in result);
  return (
    <section className="structured-result">
      <h3>{isReconciliation ? "Reconciliation report" : "Job result"}</h3>
      <dl className="structured-result__grid">
        {Object.entries(result).map(([key, value]) => (
          <div key={key}>
            <dt>{humanize(key)}</dt>
            <dd>
              {typeof value === "boolean" ? (
                <span className={value ? "result-consistent" : "result-inconsistent"}>
                  {value ? "Yes" : "No"}
                </span>
              ) : value === null ? (
                "—"
              ) : typeof value === "object" ? (
                <code>{JSON.stringify(value)}</code>
              ) : (
                displayValue(value)
              )}
            </dd>
          </div>
        ))}
      </dl>
      <details>
        <summary>Technical result data</summary>
        <pre className="json-view">{JSON.stringify(result, null, 2)}</pre>
      </details>
    </section>
  );
}

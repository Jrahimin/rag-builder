"""Record the bounded resource review without rewriting earlier audit evidence."""

import json
from pathlib import Path

root = Path(__file__).resolve().parents[2]
review = {
    "date": "2026-09-15",
    "status": "local_resource_changes_awaiting_deployment",
    "evidence": "Prior live planner consumed 2048 output tokens, all reasoning, and returned incomplete_plan.",
    "live_config_checked": {
        "generation_model": "gpt-5.6-luna",
        "embedding_model": "embed-v4.0",
        "embedding_dimensions": 1024,
        "retrieval": "hybrid",
        "reranker": "cohere",
        "output_cap": "Not exposed by read-only Configuration UI; deployment override remains unverified.",
    },
    "local_changes": {
        "llm_default_max_tokens": {"before": 4096, "after": 8192},
        "authoritative_plan_initial": {"before": 2048, "after": 4096},
        "authoritative_plan_single_retry": {"before": 4096, "after": 8192},
        "deployment_setting": "APE_LLM__MAX_TOKENS=8192; explicit lower caps still take precedence.",
    },
    "unchanged": {
        "coverage_review_tokens": 4096,
        "llm_default_timeout_seconds": 120,
        "project_last_verified_retrieval": {
            "semantic_candidates": 80, "keyword_candidates": 80,
            "rerank_window": 40, "top_k": 12,
            "max_context_chunks": 24, "context_char_budget": 48000,
            "max_chunks_per_document": 6,
        },
        "sources_and_tax_policy": "Preserved",
    },
    "limitations": [
        "Additional token headroom is not a guarantee of completion or citation quality.",
        "No evidence of embedding dimensionality, CPU/RAM, provider rate limits, or timeout exhaustion causing the observed planning failure.",
        "CPU/RAM utilization and provider quotas were not measured; no infrastructure upgrade is prescribed.",
        "Per-document exclusions warrant measurement only if missing evidence remains after correctness fixes.",
        "No new paid live generations were run for this configuration-only review.",
    ],
    "validation": {"focused_tests_passed": 15, "deselected": 93, "ruff": "passed", "format": "passed"},
    "reference": "https://developers.openai.com/api/reference/cli/resources/responses/methods/create",
}
(root / "artifacts/company-readiness/resource-review-2026-09-15.json").write_text(
    json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
log_path = root / "business-upload-log.json"
log = json.loads(log_path.read_text(encoding="utf-8"))
log["latest_resource_review"] = review
log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
report_path = root / "business-upload-report.md"
report = report_path.read_text(encoding="utf-8")
heading = "## Resource allocation review — 15 September 2026"
if heading in report:
    report = report.split(heading)[0].rstrip() + "\n"
report += """

## Resource allocation review — 15 September 2026

The earlier 2,048-token planning allowance is demonstrably insufficient for the failed broad question: reasoning exhausted the entire allowance before structured output. OpenAI counts reasoning and visible output together. Local planning now starts at up to 4,096 tokens and has one truncation retry up to 8,192. The default LLM ceiling and both environment examples now use 8,192. Every stage still respects explicit lower deployment caps; the coverage reviewer remains at 4,096. This supersedes the smaller budgets described in the preceding deployment check, without changing its historical test results.

For deployment, set `APE_LLM__MAX_TOKENS=8192` if an existing environment value overrides the new default. The live read-only Configuration page confirms GPT-5.6 Luna, Cohere embed-v4.0/1024 dimensions, and hybrid/Cohere retrieval, but does not display the effective output cap. Production adoption of the new cap is therefore not confirmed.

The last verified project allocation (80 semantic + 80 keyword candidates, 40 reranked, top K 12, up to 24 passages/48,000 characters, six chunks per document) is substantial. No blanket increase is justified by the observed failures. Embeddings, retrieval thresholds, source lifecycles, tax policy, and timeout settings are unchanged. CPU/RAM utilization and provider quotas were not measured. Additional headroom permits more work and potentially higher cost/latency; it does not repair missing sources or incorrect citation mapping.

Validation: 15 focused tests passed, 93 deselected; Ruff and formatting passed. Caps below, between, and above the new stage limits are covered, along with bounded retries and project configuration inheritance. No new live generations were run. These local changes await deployment.
"""
report_path.write_text(report, encoding="utf-8")

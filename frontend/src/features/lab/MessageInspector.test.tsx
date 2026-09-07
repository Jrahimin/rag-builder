import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type { Message } from "../../api/operatorApiClient";
import { MessageInspector } from "./TestLab";

const message: Message = {
  id: "answer",
  project_id: "project",
  conversation_id: "conversation",
  role: "assistant",
  content: "A reliable estimate is not determinable. Conditional rebate: 9,000.",
  finish_reason: null,
  input_tokens: null,
  output_tokens: null,
  prompt_version: null,
  embedding_set_version: null,
  provider: null,
  model: null,
  metadata: {},
  source_provenance: "knowledge",
  grounded: false,
  insufficient_evidence_reason: null,
  created_at: "2026-09-06T00:00:00Z",
  updated_at: "2026-09-06T00:00:00Z",
  citations: [
    {
      source_kind: "knowledge",
      filename: "Guide.pdf",
      chunk_id: "chunk",
      excerpt: "Investment provision",
    },
  ],
  claims: [
    {
      claim_id: "claim-1",
      text: "Conditional rebate: 9,000.",
      grounded: false,
      verification: "unverified",
      authority_status: "not_assessed",
      evidence: [],
    },
  ],
};

function inspect(answer: Message, expected = "", passed = false) {
  render(
    <MessageInspector
      message={answer}
      run={{
        turn: { user_message: { ...message, role: "user" }, assistant_message: answer },
        expected,
        passed,
        elapsedMs: 22771,
      }}
    />,
  );
}

describe("message grounding explanation", () => {
  test("distinguishes missing recovery telemetry from an attempted repair", () => {
    inspect(message);
    expect(screen.getByText("Authority and evidence recovery")).toBeInTheDocument();
    expect(screen.getByText(/"status": "not_reported"/)).toBeInTheDocument();
  });

  test("exposes the backend recovery and authority decisions for diagnosis", () => {
    inspect({
      ...message,
      metadata: {
        knowledge_repair: { status: "dependency_unresolved", version: "v1" },
        current_authority: { records: [{ outcome: "ungoverned_or_incomplete_metadata" }] },
      },
    });
    expect(screen.getByText(/"status": "dependency_unresolved"/)).toBeInTheDocument();
    expect(screen.getByText(/ungoverned_or_incomplete_metadata/)).toBeInTheDocument();
  });

  test("explains cited partial answers without promoting prose into a valid refusal", () => {
    inspect(message, "reliable estimate");
    expect(screen.getByRole("heading", { name: "Answer review" })).toBeInTheDocument();
    expect(screen.getByText("Cited answer — grounding incomplete")).toBeInTheDocument();
    expect(screen.getAllByText("needs attention")).toHaveLength(1);
    expect(screen.getByText(/expected words matched/)).toBeInTheDocument();
    expect(screen.getByText("0 of 1 claims supported")).toBeInTheDocument();
    expect(screen.getByText("Conditional rebate: 9,000.")).toBeInTheDocument();
    expect(screen.queryByText("Valid refusal")).not.toBeInTheDocument();
  });

  test("separates expected-word failure from successful grounding", () => {
    inspect({ ...message, grounded: true, claims: [] }, "absent phrase");
    expect(screen.getByRole("heading", { name: "Grounded answer" })).toBeInTheDocument();
    expect(screen.getByText(/expected words did not match/)).toBeInTheDocument();
    expect(screen.getByText(/This is separate from grounding/)).toBeInTheDocument();
    expect(screen.getAllByText("needs attention")).toHaveLength(1);
  });

  test("does not imply an unsupported claim when verdicts are unavailable", () => {
    inspect({ ...message, grounded: null, claims: [] });
    expect(screen.getByText("No claim verdicts available")).toBeInTheDocument();
    expect(screen.getByText(/Grounding was not established/)).toBeInTheDocument();
  });

  test("shows a backend refusal as passed even with no citations", () => {
    inspect(
      {
        ...message,
        insufficient_evidence_reason: "no_retrieval_results",
        citations: [],
        claims: [],
      },
      "",
      true,
    );
    expect(screen.getByRole("heading", { name: "Valid refusal" })).toBeInTheDocument();
    expect(screen.getByText("passed")).toBeInTheDocument();
    expect(screen.queryByLabelText("Claim verification")).not.toBeInTheDocument();
  });
});

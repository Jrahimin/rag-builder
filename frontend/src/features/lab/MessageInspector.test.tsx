import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
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
      claim_kind: "arithmetic",
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
  test("keeps all supplied passages accessible and preserves citation six numbering", () => {
    const onCite = vi.fn();
    render(
      <MessageInspector
        message={{
          ...message,
          content: "A supported rule. [6]",
          citations: Array.from({ length: 7 }, (_, i) => ({
            source_kind: "knowledge" as const,
            filename: `Source ${i + 1}`,
            chunk_id: `chunk-${i}`,
            excerpt: `Passage ${i + 1}`,
          })),
        }}
        run={null}
        activeCitation={5}
        onCite={onCite}
      />,
    );
    expect(screen.getByText("1 cited passages · 7 supplied passages")).toBeInTheDocument();
    expect(screen.getAllByText("Cited in answer")).toHaveLength(1);
    expect(screen.getAllByText("Supplied to generation; not cited")).toHaveLength(6);
    fireEvent.click(screen.getByRole("button", { name: /\[6\] Source 6/ }));
    expect(onCite).toHaveBeenCalledWith(5);
    expect(screen.getByRole("button", { name: /\[7\] Source 7/ })).toBeInTheDocument();
  });
  test("backend-classified conversational replies do not produce false grounding failures", () => {
    inspect({
      ...message,
      content: "You're welcome.",
      citations: [],
      claims: [],
      metadata: { non_knowledge_turn: true },
    });
    expect(screen.getByRole("heading", { name: "Conversational reply" })).toBeInTheDocument();
    expect(screen.queryByText("No valid citations returned")).not.toBeInTheDocument();
    expect(screen.queryByText("needs attention")).not.toBeInTheDocument();
  });

  test("a non-knowledge flag cannot hide an actual unverified claim", () => {
    inspect({ ...message, metadata: { non_knowledge_turn: true } });
    expect(screen.queryByRole("heading", { name: "Conversational reply" })).not.toBeInTheDocument();
    expect(screen.getByText("needs attention")).toBeInTheDocument();
  });

  test("shows the unresolved component and does not promote a legacy refusal pass", () => {
    inspect(
      {
        ...message,
        insufficient_evidence_reason: "unresolved_authority",
        metadata: { knowledge_repair: { coverage: { missing: ["Interest inclusion rule"] } } },
      },
      "",
      true,
    );
    expect(screen.getByText("Interest inclusion rule")).toBeInTheDocument();
    expect(screen.getByText("Task remains unanswered")).toBeInTheDocument();
    expect(screen.queryByText("passed")).not.toBeInTheDocument();
  });

  test("a supported partial answer still needs attention for the whole task", () => {
    inspect(
      {
        ...message,
        grounded: true,
        metadata: { knowledge_repair: { partial_answer: { scope: "Salary exclusion" } } },
      },
      "",
      true,
    );
    expect(screen.getByRole("heading", { name: "Partial answer" })).toBeInTheDocument();
    expect(screen.getByText("needs attention")).toBeInTheDocument();
  });

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
    expect(screen.queryByText("Answer withheld")).not.toBeInTheDocument();
  });

  test("keeps supported factual claims distinct from coverage statements and missing topics", () => {
    inspect({
      ...message,
      grounded: true,
      claims: [
        {
          claim_id: "claim-supported",
          text: "Private companies must hold an AGM.",
          grounded: true,
          verification: "supported",
          authority_status: "not_assessed",
          claim_kind: "source_assertion",
          evidence: [],
        },
        {
          claim_id: "claim-scope",
          text: "Annual return filing duty",
          grounded: true,
          verification: "supported",
          authority_status: "not_assessed",
          claim_kind: "coverage_scope",
          evidence: [],
        },
        {
          claim_id: "claim-invalid-scope",
          text: "The corpus contains no annual-return provision.",
          grounded: false,
          verification: "unsupported",
          authority_status: "not_assessed",
          claim_kind: "coverage_scope",
          verification_reason: "whole_corpus_absence_unproven",
          evidence: [],
        },
      ],
      metadata: {
        knowledge_repair: {
          partial_answer: { scope: "AGM duty" },
          coverage: { missing: ["Annual return filing duty"] },
        },
        current_authority: {
          authority_scope_status: "unresolved_relationships",
          cited: { status: "not_assessed" },
        },
      },
    });
    expect(screen.getByRole("heading", { name: "Partial answer" })).toBeInTheDocument();
    expect(screen.getByText("1 of 1 factual claims supported")).toBeInTheDocument();
    expect(screen.getByText("Annual return filing duty")).toBeInTheDocument();
    expect(
      screen.getByText(
        /This claims the whole corpus lacks a rule; a partial search does not prove that/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /Global expansion health is retrieval-wide and is not a verdict about every/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/"status": "not_assessed"/)).toBeInTheDocument();
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

  test("shows a backend refusal as unanswered even with a legacy passed run", () => {
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
    expect(screen.getByRole("heading", { name: "Answer withheld" })).toBeInTheDocument();
    expect(screen.getByText("needs attention")).toBeInTheDocument();
    expect(screen.queryByLabelText("Claim verification")).not.toBeInTheDocument();
  });

  test("distinguishes a coverage verification failure from missing evidence", () => {
    inspect({
      ...message,
      insufficient_evidence_reason: "unresolved_authority",
      metadata: {
        knowledge_repair: {
          status: "repair_unavailable",
          failure_reason: "invalid_model_response",
        },
      },
      citations: [],
      claims: [],
    });
    expect(screen.getByRole("heading", { name: "Verification failed" })).toBeInTheDocument();
    expect(screen.getByText("Task unanswered / verification failed")).toBeInTheDocument();
    expect(screen.queryByText("Task unanswered / insufficient evidence")).not.toBeInTheDocument();
  });
});

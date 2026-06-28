"""Workflow layer: deterministic multi-step AI orchestration.

Used only for complex flows (recursive processing, batch embeddings, hybrid
retrieval pipelines, evaluations). Prefer deterministic workflows
(LangGraph / state machines) over autonomous agents. Simple CRUD never needs
a workflow. Empty during the foundation sprint.
"""

"""Complex AI orchestration belongs inside feature modules.

Use ``modules/<feature>/workflows/`` for LangGraph/state-machine pipelines
(e.g. hybrid retrieval, batch indexing). Simple CRUD never needs workflows.
"""

"""Provider layer: external infrastructure behind abstract interfaces.

Every external dependency (LLM, embeddings, reranker, vector store, object
storage, OCR, connectors, ...) is accessed through an abstract base interface
so vendors can be swapped without touching business logic. Vendor SDKs must
never leak outside this layer.

Concrete providers are implemented in later sprints, for example::

    providers/llm/base_llm_provider.py
    providers/vector_store/base_vector_store_provider.py
    providers/vector_store/qdrant_provider.py
"""

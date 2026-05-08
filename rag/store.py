# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parents[1]
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====

"""
rag_agent/rag/store.py
ChromaDB-backed vector store for Retrieval-Augmented Generation.
Stores (task, code) pairs so the agent can reuse patterns from past projects.
"""

import logging
import uuid
from typing import Optional

logger = logging.getLogger("rag_agent.rag_store")

_COLLECTION_NAME = "rag_agent_tasks"
_PERSIST_DIR = "models_store/chroma"


class RAGStore:
    """
    Thin wrapper around ChromaDB for storing and querying
    (task description → generated code) pairs.
    """

    def __init__(self, persist_dir: str = _PERSIST_DIR):
        try:
            import chromadb
            from chromadb.config import Settings

            self._client = chromadb.PersistentClient(path=persist_dir)
            self._collection = self._client.get_or_create_collection(
                name=_COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(
                "RAGStore ready — collection=%s items=%d",
                _COLLECTION_NAME,
                self._collection.count(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("ChromaDB unavailable (%s) — RAG disabled", exc)
            self._collection = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, task: str, code: str) -> None:
        """Persist a (task, code) pair in the vector store."""
        if self._collection is None:
            return
        doc_id = uuid.uuid4().hex
        try:
            self._collection.add(
                ids=[doc_id],
                documents=[task],
                metadatas=[{"code": code[:4000]}],  # ChromaDB metadata size limit
            )
            logger.debug("RAGStore.add id=%s task_len=%d", doc_id, len(task))
        except Exception as exc:  # noqa: BLE001
            logger.warning("RAGStore.add failed: %s", exc)

    def query(self, task: str, n_results: int = 5) -> list[str]:
        """
        Return up to *n_results* code snippets relevant to *task*.
        Returns an empty list if the store is unavailable or empty.
        """
        if self._collection is None or self._collection.count() == 0:
            return []
        try:
            results = self._collection.query(
                query_texts=[task],
                n_results=min(n_results, self._collection.count()),
            )
            snippets: list[str] = []
            for meta_list in results.get("metadatas", []):
                for meta in meta_list:
                    code = meta.get("code", "")
                    if code:
                        snippets.append(code)
            logger.debug("RAGStore.query returned %d snippets", len(snippets))
            return snippets
        except Exception as exc:  # noqa: BLE001
            logger.warning("RAGStore.query failed: %s", exc)
            return []

    def count(self) -> int:
        if self._collection is None:
            return 0
        return self._collection.count()

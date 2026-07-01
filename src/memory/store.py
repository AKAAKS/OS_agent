"""
Semantic memory — stores past anomaly events and their fixes in ChromaDB.
Enables fix reuse when similar issues recur.
"""

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class SemanticMemory:
    """Vector store for past anomaly/fix pairs using ChromaDB."""

    def __init__(self, settings: dict, db_path: str = "data/chromadb"):
        self.similarity_threshold = settings.get("decisions", {}).get(
            "similarity_threshold", 0.78
        )
        self.db_path = db_path
        self._available = False
        self._collection = None

        try:
            import chromadb

            Path(db_path).mkdir(parents=True, exist_ok=True)
            self.client = chromadb.PersistentClient(path=db_path)
            self._collection = self.client.get_or_create_collection(
                name="fix_memory",
                metadata={"hnsw:space": "cosine"},
            )
            self._available = True
            logger.info(
                f"Semantic memory initialized ({self._collection.count()} entries)"
            )
        except ImportError:
            logger.warning("chromadb not installed — semantic memory disabled")
        except Exception as e:
            logger.error(f"Failed to initialize ChromaDB: {e}")

    @property
    def available(self) -> bool:
        return self._available

    def store_fix(
        self,
        anomaly_summary: str,
        anomaly_type: str,
        fix_applied: str,
        success: bool,
        details: dict | None = None,
    ):
        """Store a fix outcome for future retrieval."""
        if not self._available:
            return

        doc_id = f"fix_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{hash(anomaly_summary) % 10000}"

        metadata = {
            "anomaly_type": anomaly_type,
            "fix_applied": fix_applied,
            "success": str(success),
            "timestamp": datetime.now().isoformat(),
        }
        if details:
            for k, v in details.items():
                if isinstance(v, (str, int, float, bool)):
                    metadata[k] = str(v)

        try:
            self._collection.add(
                documents=[anomaly_summary],
                metadatas=[metadata],
                ids=[doc_id],
            )
            logger.info(f"Stored fix: {anomaly_type} → {fix_applied} (success={success})")
        except Exception as e:
            logger.error(f"Failed to store fix: {e}")
            # Re-initialize collection if the handle went stale
            try:
                self._collection = self.client.get_or_create_collection(
                    name="fix_memory",
                    metadata={"hnsw:space": "cosine"},
                )
                logger.info("Re-initialized ChromaDB collection after write failure")
            except Exception:
                self._available = False
                logger.warning("ChromaDB unavailable after re-init attempt — disabling semantic memory")

    def find_similar_fix(self, anomaly_summary: str) -> dict | None:
        """
        Search for a similar past anomaly and return its fix.
        Returns None if no match above the similarity threshold.
        """
        if not self._available or self._collection.count() == 0:
            return None

        try:
            results = self._collection.query(
                query_texts=[anomaly_summary],
                n_results=1,
                include=["metadatas", "distances", "documents"],
            )

            ids = results.get("ids", [[]])
            if not ids or not ids[0]:
                return None

            distances = results.get("distances", [[]])
            if not distances or not distances[0]:
                return None

            distance = distances[0][0]
            similarity = 1 - distance  # cosine distance → similarity

            if similarity >= self.similarity_threshold:
                metadatas = results.get("metadatas", [[]])
                documents = results.get("documents", [[]])
                if not metadatas or not metadatas[0]:
                    return None
                metadata = metadatas[0][0]
                if metadata.get("success") == "True":
                    logger.info(
                        f"Found similar fix (similarity={similarity:.3f}): {metadata.get('fix_applied')}"
                    )
                    return {
                        "similarity": round(similarity, 3),
                        "original_anomaly": documents[0][0] if documents and documents[0] else "",
                        "fix_applied": metadata.get("fix_applied", ""),
                        # Structured action that succeeded last time (stored by
                        # the Verify stage). Preferred over parsing fix_applied
                        # prose so replay never fabricates a shell command.
                        "action": metadata.get("action", ""),
                        "anomaly_type": metadata.get("anomaly_type", ""),
                        "timestamp": metadata.get("timestamp", ""),
                    }

        except Exception as e:
            logger.error(f"Semantic search failed: {e}")

        return None

    def get_stats(self) -> dict:
        """Get memory statistics."""
        if not self._available:
            return {"available": False, "count": 0}
        return {
            "available": True,
            "count": self._collection.count(),
            "db_path": self.db_path,
        }

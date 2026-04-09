"""
Local embeddings using sentence-transformers.
Runs on CPU — no API calls needed.
ChromaDB uses these automatically when configured, but this module
provides direct access for custom embedding needs.
"""

import logging

logger = logging.getLogger(__name__)


class LocalEmbeddings:
    """Local sentence-transformers embeddings (CPU-based, zero API cost)."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._model = None
        self._available = False

        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(model_name)
            self._available = True
            logger.info(f"Local embeddings loaded: {model_name}")
        except ImportError:
            logger.warning(
                "sentence-transformers not installed — using ChromaDB default embeddings"
            )
        except OSError as e:
            logger.warning(
                f"sentence-transformers failed to load (DLL error): {e} — using ChromaDB default embeddings"
            )

    @property
    def available(self) -> bool:
        return self._available

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts into vectors."""
        if not self._available:
            raise RuntimeError("sentence-transformers not available")
        return self._model.encode(texts).tolist()

    def embed_single(self, text: str) -> list[float]:
        """Embed a single text."""
        return self.embed([text])[0]

    def similarity(self, text1: str, text2: str) -> float:
        """Compute cosine similarity between two texts."""
        if not self._available:
            return 0.0
        from sentence_transformers import util

        emb1 = self._model.encode(text1)
        emb2 = self._model.encode(text2)
        return float(util.cos_sim(emb1, emb2)[0][0])

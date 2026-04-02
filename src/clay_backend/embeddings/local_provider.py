"""Local embedding provider using sentence-transformers."""

from __future__ import annotations

from .base import EmbeddingProvider


class LocalEmbeddingProvider(EmbeddingProvider):
    """Embedding provider using sentence-transformers (runs locally, no API key needed).

    Requires the `sentence-transformers` package:
    pip install clay-backend-plugin[local-embeddings]
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "sentence-transformers not installed. Install with: "
                "pip install clay-backend-plugin[local-embeddings]"
            )
        self._model = SentenceTransformer(model_name)
        self._model_name = model_name
        self._dimension = self._model.get_sentence_embedding_dimension()

    def embed(self, text: str) -> list[float]:
        return self._model.encode(text).tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        embeddings = self._model.encode(texts, batch_size=64, show_progress_bar=False)
        return [e.tolist() for e in embeddings]

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model_name

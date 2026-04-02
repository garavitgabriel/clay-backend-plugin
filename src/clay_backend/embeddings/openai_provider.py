"""OpenAI embedding provider using text-embedding-3-small."""

from __future__ import annotations

from .base import EmbeddingProvider


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """Embedding provider using OpenAI's text-embedding-3-small model.

    Requires the `openai` package: pip install clay-backend-plugin[openai]
    """

    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "OpenAI package not installed. Install with: "
                "pip install clay-backend-plugin[openai]"
            )
        self._client = OpenAI(api_key=api_key)
        self._model = model
        self._dimension = 1536 if "small" in model else 3072

    def embed(self, text: str) -> list[float]:
        response = self._client.embeddings.create(input=[text], model=self._model)
        return response.data[0].embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # OpenAI supports up to 2048 inputs per request
        all_embeddings: list[list[float]] = []
        batch_size = 512
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            response = self._client.embeddings.create(input=batch, model=self._model)
            all_embeddings.extend([d.embedding for d in response.data])
        return all_embeddings

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model

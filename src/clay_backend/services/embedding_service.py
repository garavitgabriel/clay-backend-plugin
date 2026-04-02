"""Embedding service — provider factory and text extraction."""

from __future__ import annotations

import json
import os

from ..embeddings.base import EmbeddingProvider

_provider: EmbeddingProvider | None = None
_initialized = False


def get_provider() -> EmbeddingProvider | None:
    """Get the configured embedding provider, or None if embeddings are disabled."""
    global _provider, _initialized

    if _initialized:
        return _provider

    _initialized = True
    provider_name = os.environ.get("EMBEDDING_PROVIDER", "").lower()
    api_key = os.environ.get("OPENAI_API_KEY", "")

    if provider_name == "openai" and api_key:
        from ..embeddings.openai_provider import OpenAIEmbeddingProvider

        _provider = OpenAIEmbeddingProvider(api_key=api_key)
    elif provider_name == "local":
        try:
            from ..embeddings.local_provider import LocalEmbeddingProvider

            _provider = LocalEmbeddingProvider()
        except ImportError:
            _provider = None
    elif api_key:
        # Auto-detect: if OPENAI_API_KEY is set, use OpenAI
        from ..embeddings.openai_provider import OpenAIEmbeddingProvider

        _provider = OpenAIEmbeddingProvider(api_key=api_key)
    # else: embeddings disabled

    return _provider


def extract_text(data: dict, embed_fields: list[str] | None = None) -> str:
    """Extract text to embed from a record's data dict.

    If embed_fields is provided, only those keys are used.
    Otherwise, the entire data dict is serialized.
    """
    if embed_fields:
        parts = []
        for field in embed_fields:
            value = data.get(field)
            if value is not None:
                if isinstance(value, (list, dict)):
                    parts.append(json.dumps(value))
                else:
                    parts.append(str(value))
        return " ".join(parts) if parts else json.dumps(data)

    # Default: serialize the full data dict as readable text
    parts = []
    for key, value in data.items():
        if isinstance(value, (list, dict)):
            parts.append(f"{key}: {json.dumps(value)}")
        else:
            parts.append(f"{key}: {value}")
    return "\n".join(parts)


def embed_text(text: str) -> list[float] | None:
    """Embed a single text string. Returns None if embeddings are disabled."""
    provider = get_provider()
    if provider is None:
        return None
    return provider.embed(text)


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """Embed a batch of text strings. Returns None if embeddings are disabled."""
    provider = get_provider()
    if provider is None:
        return None
    return provider.embed_batch(texts)


def get_dimension() -> int | None:
    """Get the embedding dimension, or None if embeddings are disabled."""
    provider = get_provider()
    if provider is None:
        return None
    return provider.dimension


def get_model_name() -> str | None:
    """Get the embedding model name, or None if embeddings are disabled."""
    provider = get_provider()
    if provider is None:
        return None
    return provider.model_name

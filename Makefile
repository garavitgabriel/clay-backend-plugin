.PHONY: install test smoke fixtures lint doctor

# Local-mode validation targets. Requires `uv`.

install:
	uv venv
	uv pip install -e ".[local-embeddings,scheduler]"
	uv pip install pytest pytest-asyncio

fixtures:
	uv run python tests/fixtures/generate_fixtures.py

test:
	EMBEDDING_PROVIDER=local uv run pytest -q

# Full loop with a real webhook receiver, no Claude Code in the way.
# Set ANTHROPIC_API_KEY to also run one real scheduler synthesis.
smoke:
	EMBEDDING_PROVIDER=local uv run python tests/smoke_local.py

lint:
	uv run ruff check src/ tests/

# First-run diagnostics: extension support, db path + counts, port ownership,
# embeddings, webhook auth. Exits 1 only on a hard failure.
doctor:
	uv run clay-backend-doctor

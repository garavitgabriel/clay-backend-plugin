# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Claude Code plugin that stores Clay (clay.com) per-record AI analysis results and enables cross-record pattern analysis. It's a plugin (not a standalone backend) — installs via `/plugin install`, uses SQLite locally, and exposes MCP tools for Claude to query.

## Commands

```bash
# Install dependencies
uv pip install -e ".[local-embeddings]"   # with local sentence-transformers
uv pip install -e ".[openai]"             # with OpenAI embeddings
uv pip install -e ".[local-embeddings,scheduler]"  # + scheduled analysis (schedules.yaml)
uv pip install -e .                       # core only (no embeddings)

# Run MCP server (normally started by Claude Code automatically)
CLAY_DATA_DIR=/tmp/test EMBEDDING_PROVIDER=local clay-mcp

# Run standalone webhook daemon
clay-webhook-daemon --port 8742 --data-dir /tmp/test

# Diagnose a broken setup (also available as the `doctor` MCP tool)
clay-backend-doctor

# Test plugin locally with Claude Code
claude --plugin-dir .

# Lint
ruff check src/

# Run tests
uv run pytest
```

## Architecture

Two entry points into the same codebase:

1. **`clay-mcp`** (`server.py:main`) — MCP server (stdio) exposing 9 tools to Claude. Also starts the webhook HTTP server as a background thread.
2. **`clay-webhook-daemon`** (`daemon.py:main`) — Standalone webhook receiver for 24/7 operation without Claude Code.

Both write to the same SQLite database at `$CLAY_DATA_DIR/clay.db`.

```
server.py (MCP tools)  ←→  services/record_service.py  ←→  database.py (SQLite + sqlite-vec)
                        ←→  services/search_service.py  ←→  embeddings/ (pluggable)
webhook_server.py (HTTP POST /webhook)  ←→  services/record_service.py (same)
```

**Key design decisions:**
- `data` column is JSON text (generic, not domain-specific). All domain concepts live inside the JSON.
- Deduplication on `(record_id, analysis_type)` — re-sending the same record updates it.
- `entity_id` enables cross-record joins (e.g., same deal analyzed by BDR and AE) but is optional.
- Embeddings are generated in batch after record commit, stored in a separate `vec_records` virtual table.
- sqlite-vec uses `WHERE embedding MATCH ? AND k = ?` syntax (not LIMIT) for KNN queries.
- The embedding dimension is fixed at vec table creation. Switching providers requires re-embedding all records.
- Vector search is **optional and probed at runtime**. pyenv-built Pythons and Apple's `/usr/bin/python3` have no `Connection.enable_load_extension`, so sqlite-vec can't load. `database.vec_available()` caches that probe; ingest and search degrade to no-embeddings mode instead of raising. Tests that need vectors carry `@requires_vec` (see `tests/conftest.py`) so the suite is green on those interpreters too.
- The MCP server and the standalone daemon can both be running. Each probes the webhook port first (`webhook_server.probe_port`) and identifies the owner via `GET /health`; a clay-backend receiver means "don't start a second one", anything else is a logged conflict.
- Claude itself is the synthesis engine — there is no LLM abstraction layer. MCP tools serve data, Claude reasons over it.

**Plugin components:**
- `.claude-plugin/plugin.json` — manifest with `userConfig` for OPENAI_API_KEY, EMBEDDING_PROVIDER, WEBHOOK_PORT
- `.mcp.json` — MCP server config, launches via `uv run clay-mcp`
- `skills/` — 3 guided workflows (import-data, analyze-patterns, setup-guide)
- `agents/synthesis.md` — subagent for deep cross-record analysis

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `CLAY_DATA_DIR` | Directory for clay.db | `~/.clay-backend` |
| `EMBEDDING_PROVIDER` | `openai`, `local`, or empty (disabled) | empty |
| `OPENAI_API_KEY` | Required if EMBEDDING_PROVIDER=openai | — |
| `WEBHOOK_PORT` | HTTP webhook server port | `8742` |
| `WEBHOOK_HOST` | Webhook bind address | `127.0.0.1` |
| `WEBHOOK_API_KEY` | If set, webhook requires `Authorization: Bearer <key>` or `X-API-Key: <key>` | — |
| `REMOTE_URL` | If set, use the hosted service instead of local SQLite | — |

**All env reads must go through `config.py`.** Never call `os.environ.get` for a
user-facing setting. The plugin loader injects unset `userConfig` values as `""`
or as the literal `"${user_config.NAME}"`, so `int(os.environ.get("WEBHOOK_PORT",
"8742"))` raises ValueError on a clean first launch and the MCP server shows
`✘ failed` with no diagnostic. `config.env_str` / `config.env_int` treat all
three shapes (absent, empty, placeholder) as unset.

`config.resolve_data_dir()` is the single source of truth for the database
location — the MCP server, the daemon, and the doctor all use it, so the two
halves of the plugin can't silently write to different `clay.db` files.

## Embedding Providers

Provider is selected lazily on first use via `embedding_service.get_provider()`. The abstract interface is in `embeddings/base.py`. OpenAI produces 1536-dim vectors, local (all-MiniLM-L6-v2) produces 384-dim. The vec table dimension is set on first record ingest and stored in the `metadata` table.

## MCP Tool Response Format

FastMCP serializes list return values as **one content block per list item**, not a single JSON array. When testing MCP tools that return lists, iterate `result.content` and parse each `content[i].text` individually.

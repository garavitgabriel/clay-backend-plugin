# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the version is below 1.0, breaking changes may land in a minor release — they
are always called out under **Changed** with migration steps.

## [0.2.0] — 2026-07-25

Setup and first-run reliability. Every fix in this release came from running the
plugin against a live Clay table over a public tunnel; each defect was environmental
or configuration, not logic. See [FINDINGS.md](FINDINGS.md) for the full engineering
record.

### ⚠️ Migration

The database location changed so that the plugin and the standalone daemon share one
file by default. Previously the plugin stored its database under the Claude Code
plugin data directory, which the separate daemon process had no way to discover.

Both halves now resolve `CLAY_DATA_DIR`, falling back to `~/.clay-backend`.

If you have existing records, move the file once:

```bash
mkdir -p ~/.clay-backend
mv ~/.claude/plugins/data/clay-backend-plugin/clay.db ~/.clay-backend/
```

Then run `clay-backend-doctor` to confirm the record count. If you prefer a different
location, set the `CLAY_DATA_DIR` plugin setting **and** pass the same path to the
daemon via `--data-dir`.

### Added

- `clay-backend-doctor` CLI and a `doctor` MCP tool — first-run diagnostics covering
  SQLite extension support, the resolved database path and record counts, webhook
  port ownership, embedding provider health, and webhook authentication. Prints the
  specific fix for each check. Exits `1` only on a hard failure.
- `CLAY_DATA_DIR` is now a plugin setting, so the data directory is configurable from
  the plugin UI rather than only by environment variable.
- Webhook responses include `total_for_type`, a running per-type count. The Clay
  column that posts the record now doubles as a progress meter.
- `GET /health` and `get_analytics` both report `db_path`, making a plugin/daemon
  database mismatch self-diagnosing.
- README: `cloudflared` as a no-account tunnel alternative, a verify-before-wiring-Clay
  step, the Clay formula-column payload pattern, a "Where Your Data Lives" section, and
  a pipeline-QA use case.
- `make doctor` target.

### Fixed

- **The MCP server no longer fails to start when plugin settings are unset.** On a
  clean launch the plugin loader injects unset `userConfig` values as an empty string
  or an unresolved `${user_config.NAME}` literal; `int("")` raised `ValueError` and the
  server died before it could report anything, showing `✘ failed` in `/mcp` with no
  diagnostic. All configuration now flows through a tolerant parser that treats
  absent, empty, and placeholder values as unset. This also prevented an unresolved
  `REMOTE_URL` placeholder — which is truthy — from silently switching the plugin into
  remote mode.
- **The plugin no longer crashes on Pythons without SQLite extension support.**
  pyenv-built interpreters and Apple's `/usr/bin/python3` have no
  `Connection.enable_load_extension`, so loading `sqlite-vec` raised `AttributeError`
  and the daemon died on a raw traceback. Vector support is now probed at runtime;
  ingest, filters, text search, webhooks, and analytics all work without it, and
  `semantic_search` explains the situation and points to the keyword-search fallback.
- **The plugin and the daemon no longer write to different databases.** Split-brain
  data directories produced a "0 records" symptom that pointed users at ingestion,
  which was working fine. Both now use one resolver, both print the absolute database
  path at startup, and a mismatch is reported explicitly. See Migration above.
- **Running the daemon and Claude Code together is now first-class.** The MCP server
  previously started its webhook thread unconditionally; when the standalone daemon
  already held the port, the bind failed silently inside a daemon thread. Each process
  now probes the port and identifies its owner over `/health` — a clay-backend receiver
  means "don't start a second one", anything else is a clear, named conflict. The
  daemon refuses a busy port with an actionable message instead of a bare `errno 48`.
- **`semantic_search` no longer returns nothing for sparse analysis types**
  ([FINDINGS #8](FINDINGS.md)). The candidate window was fixed at `top_k * 3` before
  type filtering, so a type buried under near neighbours of other types could return
  zero results — indistinguishable from "no such records exist". The window now widens
  until enough matches are found or every stored vector has been considered.
- The daemon's startup banner is unbuffered, so the database path actually reaches the
  log file under the documented `nohup clay-webhook-daemon > log 2>&1 &` pattern.

### Changed

- Documentation reframes embeddings as optional below roughly 1,000 records, rather
  than near-required. Filters and text search cover small stores completely.
- The MCP server logs to stderr at `WARNING`, keeping the stream to things a user can
  act on. (stdout is the MCP transport and must never carry human-readable output.)
- The `setup-guide` skill covers tunnels, the formula-column payload pattern, and
  troubleshooting via `doctor`.

## [0.1.0] — 2026-07-21

Initial public release: MCP server with 9 tools, pluggable embeddings (OpenAI and
local sentence-transformers), semantic search via sqlite-vec, webhook receiver,
standalone daemon, scheduled analysis, and a hosted-mode option.

[0.2.0]: https://github.com/garavitgabriel/clay-backend-plugin/releases/tag/v0.2.0
[0.1.0]: https://github.com/garavitgabriel/clay-backend-plugin/releases/tag/v0.1.0

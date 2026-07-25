# FINDINGS — clay-backend-plugin local-mode validation

Validation run on 2026-06-25. Local SQLite + sqlite-vec + local sentence-transformers
(`all-MiniLM-L6-v2`, 384-dim). No hosted/Postgres/remote paths touched. The full
loop mechanically holds (see `tests/` → `uv run pytest`, and `tests/smoke_local.py`),
but the dogfood surfaced one real logic bug and several scale/correctness smells.

> **Remediation pass 2026-07-18** — status per finding:
> - **#1 tag OR semantics — FIXED** (per-tag conditions grouped with OR; strict xfail test now a passing test)
> - **#2 scheduler cap/prompt — MITIGATED** (cap-hit warning logged; prompt char budget `CLAY_PROMPT_CHAR_BUDGET` drops oldest records with an explicit in-prompt omission note; `max_tokens` raised to 8192, per-schedule override). Full map-reduce chunking + `offset` pagination remains roadmap.
> - **#3 stale model id — FIXED** (defaults to `claude-sonnet-5`, overridable via `CLAY_SCHEDULER_MODEL` or per-schedule `model:`; hosted scheduler too)
> - **#4 created_at — FIXED** (`RecordInput.created_at` optional event time honored on insert; explicit value overwrites on resend; `ingest_csv` gained `created_at_column`; local mode only)
> - **#5 db-path drift — FIXED** (analytics size derives from `database._get_db_path()` and includes `-wal`/`-shm`)
> - **#6 scheduler extra undocumented — FIXED** (README + CLAUDE.md document `[local-embeddings,scheduler]`; Makefile `install` already pulled it)
> - **#7 non-constant-time key compare — FIXED** (`hmac.compare_digest`)
> - **#8 semantic_search type-filter under-return — FIXED 2026-07-25** (candidate window now widens until `top_k` of the requested type are collected, or until it covers every stored vector; see the note at the end of the 2026-07-24 entry)
> - **#9 deprecated dimension call — FIXED** (uses `get_embedding_dimension()` when available, falls back for older sentence-transformers)
>
> Suite after the pass: 37 passed, lint clean, smoke green.

Severity legend: **HIGH** = wrong results or breaks at realistic scale · **MED** =
silently misleading · **LOW** = cosmetic / hardening.

---

## 1. Multi-tag `query_records` filter is AND, not OR (comment says "any") — **HIGH**

- **Where:** `src/clay_backend/services/record_service.py:226-232`
- **What:** The inline comment reads *"Match records containing any of the specified
  tags"* (OR), but each tag becomes its own `tags LIKE ?` condition and all
  conditions are joined with `' AND '` (line 232). So `tags=["a","b"]` returns only
  records carrying **both** tags. Any caller passing >1 tag expecting OR gets a near-
  empty result and no error.
- **Repro:**
  ```python
  query_records(tags=["champion_identified", "no_budget"])  # -> [] (no record has both)
  query_records(tags=["champion_identified"])               # -> 12 records
  ```
- **Test:** `tests/test_clay_backend.py::test_query_filter_multiple_tags_or_semantics`
  is marked `xfail(strict=True)` pointing here. Assertions left intact, not weakened.
- **Fix sketch:** wrap the per-tag conditions in their own group joined by `OR`, then
  AND that group with the rest — or use a JSON/`EXISTS` based tag match.

## 2. Scheduler dumps EVERY fetched record into one prompt — no chunking, silent 200-cap — **HIGH**

- **Where:** `src/clay_backend/scheduler.py:88-119` (`_call_anthropic`),
  `:83` (`_fetch_records` default `limit=100`), `record_service.py:233`
  (`limit = min(limit, 200)`).
- **What:** `_call_anthropic` serializes the whole record list with
  `json.dumps(records, indent=2)` into a single user message and calls the API with
  `max_tokens=4096`. There is no token budgeting, batching, or truncation guard. Two
  compounding problems at scale:
  1. `_fetch_records` defaults to `limit=100` and `query_records` hard-caps at 200, so
     a schedule over a large table silently analyzes only the **most-recent ≤200**
     records and **nothing is logged** about the cap — the synthesis looks complete but
     isn't.
  2. Up to 200 full JSON records inlined (`indent=2`) easily runs into hundreds of KB
     of prompt; combined with `max_tokens=4096` the output is also too small to
     meaningfully summarize that volume. Beyond the cap it would overflow the context
     window outright.
- **Repro:** `tests/test_scheduler.py::test_call_anthropic_dumps_all_records_no_truncation`
  feeds 200 records and asserts every record's marker is present in the single prompt
  (nothing dropped/truncated) and that `len(user_msg) > 40_000`.
- **Fix sketch:** map-reduce/chunk records, paginate past 200 via `offset`, log when the
  cap is hit, and raise `max_tokens`.

## 3. Stale default model id — **MED**

- **Where:** `src/clay_backend/scheduler.py:94` (and `hosted/scheduler.py:125`):
  `model = schedule.get("model", "claude-sonnet-4-20250514")`.
- **What:** `claude-sonnet-4-20250514` is Claude Sonnet 4 (May 2025) — two minor
  releases behind the current `claude-sonnet-4-6`. Still a valid id today, but new
  schedules silently default to an older, less capable model and will eventually break
  when the snapshot is retired.
- **Repro:** `tests/test_scheduler.py::test_call_anthropic_builds_prompt_and_uses_default_model`
  asserts the default that reaches the API is `claude-sonnet-4-20250514`.
- **Fix:** default to `claude-sonnet-4-6` (or the latest Sonnet) and/or read from env.

## 4. `created_at` is always "now" — no way to ingest historical timestamps — **MED**

- **Where:** `record_service.py:70,98-99` stamp `created_at = _now_iso()` on every
  insert; `models.py:8-17` (`RecordInput`) has no `created_at`/date field.
- **What:** When importing a Clay export, every row's `created_at` collapses to the
  import time. All time-window features therefore filter by **ingest time, not the
  actual call/analysis date**: `query_records(since/until)` and the scheduler's
  `"7 days ago"` resolution (`scheduler.py:47-85`) are misleading for back-filled data.
  A "last 7 days" schedule run the day after a bulk import returns the entire table.
- **Repro:** Building `test_query_filter_since_until` required backdating rows with raw
  SQL because there is no supported way to set `created_at` through the ingest API.
- **Fix:** accept an optional `created_at`/event-time on `RecordInput` and honor it on
  insert; fall back to now() when absent.

## 5. Two sources of truth for the DB path (analytics size can mis-report) — **LOW**

- **Where:** `record_service.py:331-333` computes storage size from
  `os.environ["CLAY_DATA_DIR"] + "/clay.db"` directly, while every connection uses the
  cached `database._DB_PATH` (`database.py:9-18`).
- **What:** If `CLAY_DATA_DIR` changes after the first connection (the path is cached
  once), the two diverge and `storage_size_mb` reports the wrong file (or 0). Also it
  counts only `clay.db`, ignoring `-wal`/`-shm`, so size is under-reported under WAL.
- **Fix:** derive the size from `database._get_db_path()` and include WAL files.

## 6. Packaging: scheduler module can't be imported with the documented install — **LOW (setup blocker)**

- **Where:** `pyproject.toml:19-22`. The `scheduler` deps (`anthropic`, `apscheduler`,
  `pyyaml`, `httpx`) live in a separate `scheduler` extra. CLAUDE.md's documented local
  install is `uv pip install -e ".[local-embeddings]"`, which does **not** pull them,
  so `import clay_backend.scheduler` fails with `ModuleNotFoundError: anthropic`.
- **Action taken (logged per guardrails):** installed
  `uv pip install -e ".[local-embeddings,scheduler]"` to exercise the scheduler in
  tests. No source files were modified for this.
- **Fix:** document the `scheduler` extra for local scheduled-analysis use, or fold its
  deps into the default set.

## 7. Webhook API-key compared with `==` (not constant-time) — **LOW**

- **Where:** `webhook_server.py:34,38` — `auth_header == f"Bearer {api_key}"` and
  `request.headers.get("X-API-Key") == api_key`.
- **What:** Non-constant-time string comparison is a (minor) timing side channel for
  the webhook secret. Low risk over network jitter, but trivially hardened.
- **Fix:** use `hmac.compare_digest`.

## 8. `semantic_search` type-filter can under-return — **LOW** — FIXED 2026-07-25

- **Where:** `search_service.py:55` — when `analysis_type` is set it fetches
  `top_k * 3` KNN candidates then post-filters by type. If the requested type is sparse
  relative to near neighbors of other types, fewer than `top_k` results come back even
  though more matching records exist.
- **Fix applied:** sqlite-vec can't filter inside a KNN query, so the post-filter stays
  — but the candidate window now widens (×4 per round) until `top_k` of the requested
  type are collected, or until it covers every stored vector, at which point the short
  result really is everything there is. Terminates on both edges: a type with fewer
  than `top_k` rows returns what exists instead of looping, and an empty vec table
  short-circuits to `[]`.
- **Severity in practice was higher than "LOW" suggests.** With 40 near-neighbour
  records of one type and 3 of another, a `top_k=3` search for the sparse type
  returned **zero** results — not "fewer than asked for", but nothing at all, which
  reads as "no such records exist". Regression tests assert the exact record IDs come
  back; both fail against the old fixed-window code.

## 9. Deprecation warning in the local embedding provider — **LOW (cosmetic)**

- **Where:** `embeddings/local_provider.py:25` calls
  `get_sentence_embedding_dimension()`, which sentence-transformers has renamed to
  `get_embedding_dimension()` (FutureWarning on every load). Works today; will break on
  a future major.

---

## What works (validated, green)

- `ingest_records` + `ingest_csv` (incl. entity_id/entity_name column mapping).
- Dedup on `(record_id, analysis_type)` — re-send updates in place; same `record_id`
  with a different `analysis_type` is a distinct row.
- `entity_id` cross-`analysis_type` join (one entity, both call + enrichment rows).
- `query_records` filters: `analysis_type`, single `tag`, `search_data`,
  `since`/`until`, and the 200-row cap.
- `semantic_search` with local 384-dim embeddings returns correctly **ranked** results
  (near-duplicate summaries top the list; type filter respected).
- Embedding **dimension lock**: same-dim re-init is idempotent; a different dim (e.g.
  switching to OpenAI 1536) raises `ValueError` as designed (`database.py:84-90`).
- `get_analytics`, `list_analysis_types`, and `delete_records` (filter required; also
  clears matching `vec_records` rows).
- Webhook `POST /webhook`: single + batch ingest, dedup over the wire, and the
  `WEBHOOK_API_KEY` auth path (no-key open / Bearer / X-API-Key / 401 on bad creds).
- `scheduler._resolve_since()` (days/hours/weeks/months + passthrough) and
  `_fetch_records()` filtering.

---

# 2026-07-25 — Live-webhook hardening pass

Validation against a live Clay table over a public tunnel: records posted from a Clay
HTTP API column into the webhook, then queried across rows in Claude Code. The storage
and query paths held; **every defect found was environment or configuration, none was
logic.** All are fixed below.

## What held up

- **The flexible schema absorbed an unanticipated analysis type with zero code
  changes.** The workload was outbound *sequence QA* — nothing like the sales-call and
  enrichment shapes the plugin was built and tested against. No migration, no new
  columns, no code. The `data`-as-opaque-JSON decision (CLAUDE.md, "Key design
  decisions") earns its keep the moment it meets a workload nobody modelled.
- **Webhook dedup makes Clay re-runs safe.** Clay re-processes rows freely; every
  resend lands as `updated`, not a duplicate.
- **No-embeddings mode is sufficient at small record counts.** Text search plus
  filters answered every question asked of a few dozen rows. This contradicted the
  README, which framed embeddings as near-required — since corrected to "skip until
  roughly 1k records".
- **Success responses rendered in the Clay cell are the strongest UX signal in the
  integration.** `{"ingested":1,"updated":0,"errors":[]}` appearing in a Clay column
  is how an operator knows the wiring is live. Extended this pass with
  `total_for_type`, so the column doubles as a progress meter.

## Pipeline QA emerged as a use case

Asked to look across rows, Claude surfaced that the upstream Clay workflow had
silently failed on several of them —

- 3 records with an empty `emails` array (generation produced nothing),
- 2 records where `emails` arrived as a *string* rather than an array — a data-shape
  drift mid-run,
- 1 stray key present on only some rows,
- and, most usefully, **a high-value contact who matched the best-converting
  pattern exactly and had silently received no emails at all.**

None of this is visible inside Clay, where you would be eyeballing rows one at a
time. It is trivially visible once the rows sit in one queryable store. The schema
flexibility is what makes it possible: the plugin never validates a shape, so it
faithfully preserves the inconsistencies — which is exactly what lets them be found.
A stricter store would have rejected the malformed rows at ingest and their existence
would never have surfaced.

"Find the rows your workflow silently skipped" is now a headline use case in the
README, alongside sales coaching.

## Bugs found — all fixed in this pass

### F1. `enable_load_extension` AttributeError kills the daemon on first launch — **HIGH**

- **Where:** `database.py:24` — `db.enable_load_extension(True)` called bare.
- **What:** pyenv-built CPython (and, confirmed during the fix, Apple's
  `/usr/bin/python3`) is compiled without loadable-SQLite-extension support. The
  attribute simply does not exist, so the very first `get_connection()` raised
  `AttributeError` and the daemon died on a raw traceback. Field workaround was
  rebuilding the venv with a uv-managed Python.
- **Why it mattered:** embeddings are documented as optional, but an optional
  feature was taking down the whole process at import time.
- **Fixed:** `database._try_load_vec()` catches it and returns an actionable
  reason; `vec_available()` caches the probe. Ingest, query, webhook, and analytics
  all work in the degraded mode; `semantic_search` returns the reason plus the
  `query_records(search_data=...)` fallback. Daemon and MCP both announce the
  degradation at startup. Verified end-to-end against a real interpreter with the
  method stripped.

### F2. Empty/unresolved `user_config` env crashes the MCP server — **HIGH**

- **Where:** `webhook_server.py:112` and `server.py:322` —
  `int(os.environ.get("WEBHOOK_PORT", "8742"))`.
- **What:** on a clean `--plugin-dir .` launch with no user config set, `.mcp.json`
  injects `WEBHOOK_PORT` as `""` (or the literal `${user_config.WEBHOOK_PORT}`).
  `int("")` → `ValueError` → `/mcp` shows `✘ failed` with no diagnostic whatsoever.
- **Why it mattered:** this is *the* first-run path. The plugin failed for a new
  user before they had configured anything, and told them nothing.
- **Fixed:** new `config.py` with `env_str`/`env_int` treating absent, empty, and
  `${...}` placeholder values as unset. Applied to every user_config-sourced
  variable: `WEBHOOK_PORT`, `WEBHOOK_HOST`, `WEBHOOK_API_KEY`, `EMBEDDING_PROVIDER`,
  `OPENAI_API_KEY`, `REMOTE_URL`, `REMOTE_API_KEY`, plus `CLAY_DATA_DIR` and the
  scheduler's variables. Note `REMOTE_URL` mattered doubly: an unresolved
  placeholder is truthy, so it would have flipped the plugin into remote mode.

### F3. Split-brain database — daemon and plugin on different files — **HIGH**

- **Where:** `daemon.py:46` defaulted `--data-dir` to `.` (CWD) while `.mcp.json`
  set `CLAY_DATA_DIR=${CLAUDE_PLUGIN_DATA}`.
- **What:** records ingested by the daemon landed in `<cwd>/clay.db`; the plugin
  read a different file and reported 0 records. It only appeared to work when Claude
  Code happened to be launched from the same directory the daemon ran in — pure luck
  of the CWD.
- **Why it mattered:** the worst possible failure mode. No error, no warning, and
  the symptom ("0 records") points the user at ingestion, which is working fine.
- **Fixed:** `config.resolve_data_dir()` is now the single resolver for both halves
  — `CLAY_DATA_DIR`, else the shared default `~/.clay-backend`. Both print the
  absolute DB path at startup (MCP to **stderr**, since stdout is the MCP
  transport). `GET /health` and `get_analytics` both return `db_path`. When the MCP
  server finds a daemon on its port with a *different* `db_path`, it says so
  explicitly.
- **Also required a manifest change.** Unifying the resolver was not enough on its
  own: `.mcp.json` injected `CLAY_DATA_DIR=${CLAUDE_PLUGIN_DATA}`, a path the
  separate daemon process has no way to discover, so an *installed* plugin would
  still have split-brained even with one resolver. `CLAY_DATA_DIR` is now a
  `userConfig` entry that defaults to empty, letting the shared `~/.clay-backend`
  default apply to both halves. Migration for existing v0.1.0 users is one `mv`,
  documented in the README.

### F4. Both processes race for the webhook port, silently — **MED**

- **Where:** `server.py:360-362` — the MCP server unconditionally started its
  webhook thread.
- **What:** with the standalone daemon already on 8742, the uvicorn bind failed
  inside a daemon thread. No visible error; the user cannot tell which process owns
  the port. Harmless in practice by luck, confusing by design.
- **Why it mattered:** daemon + interactive sessions is a legitimate, documented
  topology. It should be first-class, not accidental.
- **Fixed:** `webhook_server.probe_port()` identifies the port's owner via
  `GET /health`. A clay-backend receiver → log "not starting a second receiver" and
  continue; anything else → a clear conflict warning naming `WEBHOOK_PORT`. The
  daemon does the same check in reverse and exits 1 with an actionable message
  rather than uvicorn's bare `errno 48`.

### F5 (new capability). `clay-backend-doctor` — **first-run diagnostics**

Would have caught all four of the above in under ten seconds. Checks mode, SQLite
extension support, resolved DB path + per-type counts, webhook port ownership
(including split-brain detection), embedding provider health, and webhook auth —
printing the specific fix for each. Available as a CLI and as the `doctor` MCP
tool. Exits 1 only on hard failure.

## Documentation gaps closed

Every one of these cost trial-and-error time to rediscover:

- **`cloudflared` as the no-account tunnel alternative** to ngrok.
- **A verify-before-wiring-Clay step**: `curl /health`, then one authenticated
  `curl -X POST /webhook` smoke record. Splits "tunnel broken" from "API key wrong"
  from "Clay column misconfigured" before any Clay config exists.
- **The Clay payload pattern that actually worked**: build the object in a Clay
  **formula column** via `JSON.stringify({...})` with merge tokens, then reference
  that single column in the HTTP API body via `Clay.formatForJSON(...)`. Hand-escaped
  JSON in the body editor is where the string-vs-array drift (see above) came from —
  the formula column keeps arrays as arrays and escapes quotes and newlines
  correctly. Exact `Authorization: Bearer` header shape documented.
- **Embeddings reframed** as "skip until ~1k records" rather than near-required.
- **Data directory documented** as a first-class concept with its resolution order.

## Follow-ups (not fixed in this batch)

- **Hosted mode is the real answer to tunnel friction.** Free-tier tunnel URLs die
  with the process and the endpoint is hardcoded in the Clay column, so every restart
  means editing Clay. A permanent URL makes Clay config a one-time setup. The
  `hosted/` path is already scaffolded.
- **`total_for_type` in the webhook response** was added this batch (the Clay column
  now doubles as a progress meter), but the equivalent is not yet in the MCP
  `ingest_records` response.
- **The pattern generalizes beyond Clay.** Per-row AI → webhook → queryable local
  store describes Gong call analyses, Marketo scoring, Jira triage bots — anything
  emitting per-record AI output has the same aggregation gap. The plugin is a
  reference implementation of a general pattern.

## Also closed in this batch

**FINDINGS #8** (`semantic_search` type-filter under-return) was carried over from the
2026-06-25 validation run as OPEN/low-impact. Writing the regression test showed it was
worse than catalogued — a sparse type buried under near neighbours returned *zero*
results, not merely fewer than `top_k`, which is indistinguishable from "no such records
exist". Fixed alongside the field-test batch; see finding #8 above.

**Suite after this batch:** 99 passed, lint clean, smoke green. On an interpreter
without SQLite extension support: 89 passed, 10 skipped (`@requires_vec`).

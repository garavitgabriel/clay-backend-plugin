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
> - **#8 semantic_search type-filter under-return — OPEN** (roadmap; low impact)
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

## 8. `semantic_search` type-filter can under-return — **LOW**

- **Where:** `search_service.py:55` — when `analysis_type` is set it fetches
  `top_k * 3` KNN candidates then post-filters by type. If the requested type is sparse
  relative to near neighbors of other types, fewer than `top_k` results come back even
  though more matching records exist.
- **Fix:** loop/expand `k` until `top_k` of the requested type are collected, or filter
  inside the vec query.

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

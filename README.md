# Clay Backend Plugin

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](pyproject.toml)

A Claude Code plugin that fills Clay's aggregation gap. Clay analyzes data one row at a time — this plugin stores those results, adds semantic search, and lets Claude find patterns across all your records.

> "Insufficient discovery in 8 of 10 calls this week. Budget qualification is the #1 gap — and it's getting worse."
>
> "3 of the last 5 company enrichments flagged a funding round, but none of those leads got routed to enterprise AEs."
>
> "Support ticket sentiment went negative across 4 accounts this month — all tied to the same onboarding gap."
>
> These insights are impossible inside Clay. They require looking across rows. This plugin makes them automatic.

## Quick Start

In Claude Code:

```
/plugin marketplace add garavitgabriel/clay-backend-plugin
/plugin install clay-backend-plugin@garavitgabriel
```

Tell Claude: **"I have a Clay export to analyze"** — the plugin guides you through import, then you ask questions.

## How It Works

```
Clay (per-record AI analysis)
    ↓ webhook (automatic) or CSV export (manual)
Clay Backend Plugin
    ├── Webhook server receives data as Clay processes each row
    ├── Stores records locally (SQLite)
    ├── Generates embeddings for semantic search
    └── Claude queries via MCP tools → synthesizes insights directly
```

No Docker. No external database. No server to manage.

---

## Getting Data In

### Option 1: Live Webhooks (Recommended)

Clay sends data automatically as each row is processed.

```
You: "How do I connect Clay?"
Claude: → Shows webhook URL + exact JSON body template for Clay's HTTP API column
```

In Clay, add an **HTTP API** enrichment column to your table:

| Setting | Value |
|---------|-------|
| Method | POST |
| URL | `http://localhost:8742/webhook` |
| Body | See below |

```json
{
  "record_id": "{{Row ID}}",
  "analysis_type": "call_analysis",
  "data": {
    "summary": "{{AI Summary}}",
    "score": "{{Score}}",
    "rep": "{{Rep Name}}"
  },
  "entity_id": "{{Deal ID}}",
  "entity_name": "{{Company Name}}"
}
```

Each time Clay processes a row, the result flows into the plugin automatically.

**Connecting Clay Cloud to your local machine:**

Clay runs in the cloud, so it can't reach `localhost` directly. You need a tunnel. Either works:

```bash
# Option A: cloudflared — no account, no signup, one command
brew install cloudflared
cloudflared tunnel --url http://127.0.0.1:8742

# Option B: ngrok — free account required
brew install ngrok
ngrok config add-authtoken <your-token>    # free account at ngrok.com
ngrok http 8742
```

Both print a public HTTPS URL. Use it plus `/webhook` in Clay (e.g. `https://abc123.trycloudflare.com/webhook`).

Note that on the free tier of either tool the URL changes every time the tunnel restarts, and you'll have to update the Clay column. [Hosted mode](#hosted-mode-optional) gives you a permanent URL instead.

**Verify before wiring Clay.** Two curls save a lot of guessing — do these while the tunnel is running, before you touch the Clay column:

```bash
# 1. Does the tunnel reach your machine at all?
curl https://<your-tunnel-host>/health
# -> {"status":"ok","service":"clay-backend-webhook","db_path":"/Users/you/.clay-backend/clay.db"}

# 2. Does an authenticated write land?
curl -X POST https://<your-tunnel-host>/webhook \
  -H "Authorization: Bearer $WEBHOOK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"record_id":"smoke-1","analysis_type":"smoke_test","data":{"hello":"world"}}'
# -> {"ingested":1,"updated":0,"errors":[],"total_for_type":{"smoke_test":1}}
```

If step 1 fails, the problem is the tunnel. If step 2 returns `401`, the problem is the API key. If both pass, anything that breaks afterward is the Clay column config — a much smaller search space. Clean up with "delete the smoke_test records" in Claude Code.

Stuck? Run `clay-backend-doctor` (see [Diagnostics](#diagnostics)).

**Security:** Set `WEBHOOK_API_KEY` in the plugin config when exposing via a tunnel. The webhook endpoint will require `Authorization: Bearer <key>` on all requests. Ask Claude "what's the webhook URL?" — it will show the exact header to add in Clay.

The webhook server binds to `127.0.0.1` (this machine only) by default — tunnels connect to localhost, so nothing more is needed. Set `WEBHOOK_HOST=0.0.0.0` (or `clay-webhook-daemon --host 0.0.0.0`) only if another device on your network must reach it directly, and set `WEBHOOK_API_KEY` if you do.

#### Clay integration pattern (what actually works in production)

Hand-writing JSON in Clay's HTTP API body editor works for simple payloads, but breaks down fast: merge tokens containing quotes or newlines corrupt the JSON, and array columns arrive as strings like `"['a@x.com', 'b@x.com']"` instead of real arrays. Both failure modes are silent — Clay reports a 200 and you find out later that half your rows are malformed.

The reliable pattern is **build the payload in a formula column, then reference that one column in the body**:

**Step 1** — add a Clay **formula column** (call it `Webhook Payload`) that builds the whole object in JavaScript, where merge tokens are real values rather than text substituted into a string:

```javascript
JSON.stringify({
  record_id: {{Row ID}},
  analysis_type: "sequence_qa",
  entity_id: {{Company Domain}},
  entity_name: {{Company Name}},
  data: {
    prospect_title: {{Title}},
    emails: {{Generated Emails}},        // stays a real array
    ai_summary: {{AI Analysis}},         // quotes/newlines escaped correctly
    score: {{Fit Score}}
  }
})
```

**Step 2** — in the **HTTP API** column, reference that single column as the entire body:

| Setting | Value |
|---------|-------|
| Method | `POST` |
| URL | `https://<your-tunnel-host>/webhook` |
| Headers | `Authorization: Bearer <your WEBHOOK_API_KEY>`<br>`Content-Type: application/json` |
| Body | `Clay.formatForJSON({{Webhook Payload}})` |

Why this is better: `JSON.stringify` handles all escaping, arrays stay arrays, the payload is inspectable in its own column before it's ever sent, and changing the shape means editing one formula instead of re-escaping a body template.

The response lands back in the HTTP API column's cell, so the Clay table doubles as a progress meter:

```json
{"ingested": 1, "updated": 0, "errors": [], "total_for_type": {"sequence_qa": 17}}
```

`updated: 1` instead of `ingested: 1` means Clay re-ran a row it had already sent — that's deduplication working, not an error.

### Option 2: CSV Import

Export your Clay table as CSV, then:

```
You: "Import this CSV of call analyses" → drop file path
Claude: "Which column has the row ID? What should we call this analysis type?"
→ Maps columns → imports 47 records → "Done. Ask me to find patterns!"
```

### Option 3: Paste JSON

Copy structured analysis output from Clay and paste it directly. Claude will parse and import it.

---

## What You Can Do

### Ask Questions (Interactive)

Works with any type of Clay data — sales calls, company enrichment, support tickets, lead scoring, email analysis, content research, competitive intel.

**Find patterns:**
```
"What patterns do you see across this week's prospect enrichments?"
"What are the common objections in our last 30 sales calls?"
"Which support issues keep recurring across these tickets?"
```

**Semantic search:**
```
"Find companies where funding was mentioned"
"Show me leads that expressed urgency"
"Which enrichments flagged compliance concerns?"
```

**Cross-type comparison:**
```
"Compare our enrichment data vs actual call outcomes for the same companies"
"How does email sentiment correlate with deal stage for these accounts?"
"Show me where lead scoring disagrees with rep assessments"
```

**Track trends:**
```
"Is lead quality getting better or worse over the last 3 weeks?"
"How has competitor mention frequency changed this quarter?"
"Are support ticket topics shifting month over month?"
```

### Automated Analysis (Scheduled)

Run the daemon with a `schedules.yaml` config — skills execute on cron and deliver results to Slack or back to Clay.

```bash
clay-webhook-daemon --schedules schedules.yaml
```

```yaml
schedules:
  # Weekly sales coaching digest → Slack every Monday
  - name: weekly-coaching-digest
    skill: analyze-patterns
    cron: "0 8 * * 1"
    context:
      analysis_type: call_analysis
      since: "7 days ago"
    prompt: "Per-rep coaching priorities with specific evidence."
    outputs:
      - type: slack
        webhook_url: https://hooks.slack.com/services/XXX

  # Daily lead quality alert → Slack if something's off
  - name: lead-quality-check
    skill: analyze-patterns
    cron: "0 18 * * *"
    context:
      analysis_type: lead_scoring
      since: "1 day ago"
    prompt: "Alert only if average quality dropped below B."
    outputs:
      - type: slack
        webhook_url: https://hooks.slack.com/services/XXX

  # Monthly enrichment review → back to Clay for routing
  - name: monthly-enrichment-review
    skill: analyze-patterns
    cron: "0 9 1 * *"
    context:
      analysis_type: company_enrichment
      since: "30 days ago"
    outputs:
      - type: clay_webhook
        url: https://your-clay-webhook-table
```

Skills are the playbook — same `analyze-patterns` skill works interactively in Claude Code AND automated via the scheduler. Write once, use both ways.

Outputs go to **Slack** (incoming webhook) or **Clay** (webhook table → Clay routes to Slack, email, CRM, or any other destination using Clay's own delivery).

---

## Always-On Mode (Optional)

By default, the webhook server runs while Claude Code is open. If you want data to accumulate 24/7 — even overnight or on weekends — run the standalone daemon:

```bash
clay-webhook-daemon
```

This runs the webhook receiver as a separate background process. Same database — when you open Claude Code next, all records are already there.

On startup it prints the absolute path of the database it opened — check that line matches what `get_analytics` reports inside Claude Code. See [Where Your Data Lives](#where-your-data-lives).

```
Clay webhook daemon starting on http://127.0.0.1:8742
Database:         /Users/you/.clay-backend/clay.db
Webhook endpoint: http://127.0.0.1:8742/webhook
Health check:     http://127.0.0.1:8742/health
```

```bash
# Custom port and data directory (set CLAY_DATA_DIR for Claude Code to match)
clay-webhook-daemon --port 9000 --data-dir ~/clay-data

# Run in background on Mac — keep the log, it has the DB path in it
nohup clay-webhook-daemon > ~/.clay-backend/daemon.log 2>&1 &
```

Running the daemon and Claude Code at the same time is fine — see [Running the Daemon and Claude Code Together](#running-the-daemon-and-claude-code-together).

---

## Embeddings

**Skip this until you pass roughly 1,000 records.** Embeddings are the most common setup-anxiety item and the least necessary one at the start. At small record counts, `query_records` filters plus text search answer essentially everything — a table of a few dozen rows is fully served with embeddings switched off.

Embeddings buy you one thing: finding records by *meaning* rather than keyword. That matters when you have enough records that you can no longer scan them, and when the wording varies ("no budget", "pricing is a stretch", "need to check with finance" all being the same concern). Turn them on then.

| Provider | Cost | Setup |
|----------|------|-------|
| **None** (start here) | — | Do nothing. Filters and text search still work. |
| **OpenAI** | ~$0.008 / 1,000 records | Set `OPENAI_API_KEY` in plugin config |
| **Local** | Free | `uv pip install -e ".[local-embeddings]"` (~80MB model) |

Configure via plugin settings after install. Switching providers later means re-embedding everything (the vector dimension is fixed at first ingest), so if you know you'll want OpenAI eventually, it's slightly cheaper to start there than to migrate.

**Requires SQLite extension support.** Vector search needs `sqlite-vec`, which needs a Python built with loadable-extension support. pyenv-built Pythons and Apple's `/usr/bin/python3` are not — on those, the plugin runs fine but semantic search is off and says so. Fix by rebuilding the venv on a uv-managed Python:

```bash
uv venv --python 3.12 --managed-python
```

`clay-backend-doctor` tells you which situation you're in.

---

## Where Your Data Lives

Both halves of the plugin — the MCP server inside Claude Code and the standalone `clay-webhook-daemon` — resolve the database path the same way:

1. `CLAY_DATA_DIR` if set
2. otherwise `~/.clay-backend/`

The database is `<data dir>/clay.db`. Because both use the same rule, the daemon and your Claude Code session land on the same file by default no matter which directory you launch them from.

If you override it, override it for both — the `CLAY_DATA_DIR` plugin setting for Claude Code, and `--data-dir` for the daemon:

```bash
export CLAY_DATA_DIR=~/clay-data          # or
clay-webhook-daemon --data-dir ~/clay-data
```

> **Upgrading from v0.1.0?** Earlier builds stored the plugin's database under the Claude Code plugin data directory (`~/.claude/plugins/data/clay-backend-plugin/clay.db`), which the standalone daemon had no way to find. Both halves now default to `~/.clay-backend`. To keep existing records, move the file once:
>
> ```bash
> mkdir -p ~/.clay-backend
> mv ~/.claude/plugins/data/clay-backend-plugin/clay.db ~/.clay-backend/
> ```
>
> Run `clay-backend-doctor` afterwards to confirm the record count.

A mismatch is the "I ingested 16 records but the plugin says 0" failure. It's now visible from three places rather than silent:

- The daemon prints its absolute DB path at startup.
- `GET /health` returns `db_path`.
- `get_analytics` returns `db_path` alongside the counts.
- `clay-backend-doctor` compares them and tells you if they disagree.

---

## Diagnostics

```bash
clay-backend-doctor
```

Or ask Claude: **"run the doctor"** — it's an MCP tool too.

It checks the six things that actually go wrong on first run, and prints the fix for each:

```
clay-backend doctor
============================================================
[PASS] mode
       local — records stored in SQLite on this machine
[PASS] sqlite extension support
       OK — sqlite 3.50.4, sqlite-vec loads
[WARN] database
       /Users/you/.clay-backend/clay.db (from default) — 0 record(s): no records yet
       fix: No records stored. If you expected some, confirm the daemon writes here
            too — it prints its database path at startup, and it must match.
[WARN] webhook port
       a clay-backend daemon owns 127.0.0.1:8742 but writes to /tmp/other/clay.db,
       while this process reads /Users/you/.clay-backend/clay.db
       fix: Split-brain database. Start the daemon with the same directory:
            clay-webhook-daemon --data-dir /Users/you/.clay-backend
[PASS] embeddings
       disabled — filters and text search still work
[PASS] webhook auth
       WEBHOOK_API_KEY is set — /webhook requires Authorization: Bearer <key>
============================================================
4 passed, 2 warning(s), 0 failure(s)
```

Exit code is `1` only on a hard failure; warnings exit `0`.

---

## Running the Daemon and Claude Code Together

Running `clay-webhook-daemon` 24/7 *and* using Claude Code interactively is a supported setup — they share one database.

When the plugin starts and finds its webhook port already held, it checks who owns it:

- **A clay-backend daemon** → logs `external webhook daemon detected on port N — not starting a second receiver` and carries on. The daemon keeps receiving; the plugin just reads.
- **Anything else** → logs a clear conflict warning and continues without a webhook endpoint. Set `WEBHOOK_PORT` to something free, or stop the other process.

The daemon does the same check in reverse and refuses to start on a busy port with an actionable message rather than a bare `errno 48`.

---

## MCP Tools

10 tools available to Claude:

| Tool | What it does |
|------|-------------|
| `ingest_records` | Import JSON records. Deduplicates automatically. |
| `ingest_csv` | Import a CSV file with column mapping. |
| `query_records` | Filter records by type, entity, tags, date range, or text search. |
| `semantic_search` | Natural language search across records using embeddings. |
| `get_record` | Fetch a single record by ID. |
| `list_analysis_types` | Show what types of data are stored with counts. |
| `get_analytics` | Summary stats, top entities, storage size, and the resolved DB path. |
| `get_webhook_url` | Get the webhook URL and Clay configuration template. |
| `delete_records` | Remove records by type, date, or ID (requires at least one filter). |
| `doctor` | Run environment diagnostics — see [Diagnostics](#diagnostics). |

## Skills

| Skill | When it triggers |
|-------|-----------------|
| `/import-data` | User wants to import Clay data (CSV, JSON, or paste) |
| `/analyze-patterns` | User asks about patterns, trends, comparisons, coaching priorities |
| `/setup-guide` | User asks how to set up or get started |

---

## Data Schema

Records use a **flexible JSON schema** — not tied to sales, support, or any specific domain:

```json
{
  "record_id": "row-001",
  "analysis_type": "call_analysis",
  "data": { "any": "JSON you want" },
  "entity_id": "deal-100",
  "entity_name": "Acme Corp",
  "tags": ["discovery", "qualified"],
  "source": "gong"
}
```

Only `record_id`, `analysis_type`, and `data` are required. Everything else is optional.

**Event time (`created_at`)**: Optionally include `created_at` (ISO 8601) when the analyzed event happened earlier than the import — e.g. back-filling a month of call analyses. Time-window queries (`since`/`until`, scheduled "last 7 days" runs) filter on this field, so without it, bulk imports all collapse to the import date. CSV imports can map it with `created_at_column`.

**Deduplication**: Records are uniquely identified by `record_id` + `analysis_type`. Sending the same combination again updates the existing record.

**Cross-record joins**: Use `entity_id` when the same deal/company/prospect is analyzed in multiple tables (e.g., BDR call analysis + AE call analysis + email sentiment). This is what enables cross-stage comparison.

---

## Use Cases

The plugin works with any Clay workflow that produces per-record AI analysis. The `data` field is flexible JSON — the plugin doesn't care what's inside it.

### Pipeline QA — find the rows your workflow silently skipped

The one nobody designs for and everybody needs. Clay workflows fail *per row*, quietly: an enrichment returns nothing, a generation step times out, a column changes shape halfway through a run. In Clay you'd have to eyeball hundreds of rows to notice. Once the rows are in one queryable store, you just ask.

```
"Which rows in this batch are missing outputs, and is there a pattern?"

→ 3 of 16 rows have an empty emails array — all three are the rows where
  the title field was blank, so the generation prompt had nothing to work
  with. One of them is the CFO at Acme, who otherwise matches your
  best-converting pattern exactly.

  Separately: 2 rows have emails as a string ("['a@x.com']") rather than
  an array. Those came in before you switched the Clay column to a
  formula — they'll break anything that iterates them.
```

The flexible schema is what makes this possible: the plugin never validates a shape, so it faithfully stores the inconsistencies your workflow produced — which is exactly what lets Claude find them. A stricter store would have rejected the malformed rows at ingest and you'd never learn they existed.

### Sales Coaching

Clay analyzes Gong call transcripts → plugin finds team-wide coaching gaps.

```
"What patterns do you see in last week's calls?"

→ Budget qualification skipped in 65% of calls — team-wide, not one rep.
  Bob's calls average score 34 (team avg 62) — skips discovery entirely.
  Lead quality dropped from B to C+ — 9 of 11 D-grade leads came from
  the same webinar campaign.
```

Schedule a weekly Slack digest every Monday → manager walks in knowing the priorities.

### Company Enrichment & Lead Routing

Clay enriches companies (funding data, tech stack, headcount) → plugin spots patterns in the enriched data.

```
"Which enriched companies from this week look like enterprise prospects
 but got routed to SMB reps?"

→ 7 companies flagged: all have 500+ employees and recent Series C,
  but scored as SMB because the scoring model weights domain traffic
  over headcount. Recommend adjusting the routing threshold.
```

### Support Ticket Analysis

Clay runs sentiment and topic analysis on Zendesk tickets → plugin finds systemic issues.

```
"What are the top support themes this month and are they getting worse?"

→ Onboarding friction is #1 (34% of tickets). It's concentrated in
  accounts that signed up after the March UI update. 8 of 12 mention
  "can't find the settings page" — likely a navigation regression.
```

### Competitive Intelligence

Clay monitors competitor mentions across calls, emails, and news → plugin tracks share of voice.

```
"How often is [Competitor X] showing up, and in what context?"

→ Competitor X mentioned in 18 of 45 calls this month, up from 9 last
  month. Context shifted: previously mentioned as "also evaluating,"
  now mentioned as "showed us a demo." They're getting more aggressive
  in our mid-market segment.
```

### Email Campaign Analysis

Clay scores email replies for sentiment and intent → plugin measures campaign effectiveness.

```
"Compare reply sentiment across our last 3 outbound campaigns"

→ Campaign A (case study angle): 72% positive replies, 15% meetings booked
  Campaign B (ROI calculator): 45% positive, 8% meetings
  Campaign C (cold intro): 23% positive, 3% meetings
  Clear winner. Campaign A's approach should be the template.
```

### Cross-Stage Pipeline

Multiple Clay tables (BDR calls + AE calls + lead scores) linked by deal ID → plugin joins them.

```
"Where do deals break between BDR qualification and AE discovery?"

→ 6 of 12 matched deals have a qualification mismatch. BDRs rated them
  B+ based on enthusiasm. AEs found no confirmed budget in 5 of 6.
  Root cause: BDRs treat "we have budget for tools like this" as
  confirmed. They need to ask for a specific number.
```

---

## Architecture

```
Plugin
├── .claude-plugin/plugin.json    Plugin manifest + user config
├── .mcp.json                     MCP server config (auto-starts)
├── skills/                       Guided workflows (3 skills)
├── agents/                       Synthesis subagent
└── src/clay_backend/
    ├── server.py                 MCP server (10 tools) + webhook startup
    ├── webhook_server.py         HTTP POST /webhook + GET /health + port probe
    ├── daemon.py                 Standalone webhook CLI
    ├── database.py               SQLite + sqlite-vec (degrades without it)
    ├── config.py                 Tolerant env parsing + shared data-dir rule
    ├── doctor.py                 First-run diagnostics (CLI + MCP tool)
    ├── models.py                 Pydantic models
    ├── services/
    │   ├── record_service.py     Store, deduplicate, query, aggregate
    │   ├── embedding_service.py  Provider factory + text extraction
    │   └── search_service.py     Vector similarity search
    └── embeddings/
        ├── base.py               Abstract provider interface
        ├── openai_provider.py    text-embedding-3-small (1536 dims)
        └── local_provider.py     all-MiniLM-L6-v2 (384 dims)

Data: $CLAY_DATA_DIR/clay.db, else ~/.clay-backend/clay.db
Webhook: http://localhost:8742/webhook
```

---

## Hosted Mode (Optional)

Don't want to deal with ngrok? Deploy the webhook receiver as a tiny hosted service and get a permanent URL.

**Deploy to Railway/Render:** the repo includes `railway.toml`, `nixpacks.toml`, and `hosted/render.yaml` — point either platform at the repo and it builds the `hosted/` service. Set `DATABASE_URL` (PostgreSQL) and `API_KEY` in the environment.

**Or deploy manually:**
```bash
cd hosted/
# Set DATABASE_URL to a PostgreSQL instance
# Set API_KEY for authentication
docker build -t clay-backend .
docker run -p 8000:8000 -e DATABASE_URL=... -e API_KEY=... clay-backend
```

Then configure the plugin to use remote mode:
```
REMOTE_URL=https://your-app.railway.app
REMOTE_API_KEY=your-api-key
```

In remote mode, the plugin queries the hosted service instead of local SQLite. No local webhook server, no ngrok — Clay posts directly to your permanent URL.

| | Local Mode | Remote Mode |
|--|-----------|-------------|
| Storage | SQLite on your machine | PostgreSQL on server |
| Webhook URL | localhost (needs ngrok) | Permanent public URL |
| Semantic search | Embeddings via OpenAI/local | Text search (embeddings coming) |
| Cost | Free | ~$5/mo (Railway/Render) |
| Always-on | Needs daemon running | Yes, 24/7 |

---

## Development

```bash
git clone https://github.com/garavitgabriel/clay-backend-plugin.git
cd clay-backend-plugin

# Install everything (venv + local embeddings + scheduler + pytest)
make install
# — or manually:
uv venv && uv pip install -e ".[local-embeddings,scheduler]"

# Test with Claude Code
claude --plugin-dir .

# Run tests / lint / end-to-end smoke (webhook → ingest → search)
make test
make lint
make smoke
```

> The `scheduler` extra (anthropic, apscheduler, pyyaml, httpx) is required for
> `clay_backend.scheduler` — scheduled analysis via `schedules.yaml`. The plain
> `[local-embeddings]` install covers everything else.

## Roadmap

- [x] MCP server with 10 tools (store, query, search, analytics, doctor)
- [x] Pluggable embeddings (OpenAI + local sentence-transformers)
- [x] Semantic search via sqlite-vec
- [x] Webhook HTTP server (receives Clay data automatically)
- [x] Standalone webhook daemon (24/7 collection)
- [x] Skills for guided import and analysis
- [x] Synthesis agent for deep cross-record analysis
- [x] Hosted backend option (FastAPI + PostgreSQL) with one-click deploy
- [x] Scheduled skill execution (cron → Anthropic API → Slack/Clay delivery)
- [ ] Embeddings support in hosted mode (pgvector)
- [ ] Email output for scheduled reports
- [ ] Map-reduce chunking for scheduled synthesis over very large record sets (current: prompt budget with explicit omission note)
- [x] `semantic_search` type-filter candidate expansion (sparse types no longer return fewer than `top_k`)

## Changelog

See [CHANGELOG.md](CHANGELOG.md). **Upgrading from v0.1.0 requires moving your database file** — one command, documented there and under [Where Your Data Lives](#where-your-data-lives).

## Contributing

Issues and PRs welcome. Run `make install && make test && make lint` before submitting — the CI runs the same suite. `make doctor` diagnoses a broken local setup.

## License

[MIT](LICENSE)

---

Built by [Gabriel Garavit](https://github.com/garavitgabriel).

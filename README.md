# Clay Backend Plugin

A Claude Code plugin that fills Clay's aggregation gap. Clay analyzes data one row at a time — this plugin stores those results, adds semantic search, and lets Claude find patterns across all your records.

> "Insufficient discovery in 8 of 10 calls this week. Budget qualification is the #1 gap — and it's getting worse."
>
> That insight is impossible inside Clay. It requires looking across rows. This plugin makes it automatic.

## Quick Start

```
/plugin install clay-backend-plugin
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

Clay runs in the cloud, so it can't reach `localhost` directly. Use [ngrok](https://ngrok.com) to create a secure tunnel:

```bash
# Install and authenticate (one-time)
brew install ngrok
ngrok config add-authtoken <your-token>    # free account at ngrok.com

# Start tunnel
ngrok http 8742
```

Use the ngrok URL (e.g., `https://abc123.ngrok-free.app/webhook`) in Clay instead of localhost.

**Security:** Set `WEBHOOK_API_KEY` in the plugin config when exposing via ngrok. The webhook endpoint will require `Authorization: Bearer <key>` on all requests. Ask Claude "what's the webhook URL?" — it will show the exact header to add in Clay.

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

## What You Can Ask

**Find patterns:**
```
"What are the common issues across this week's call analyses?"
→ Fetches all records → identifies 3 recurring themes, 2 outliers,
  specific coaching recommendations with record citations
```

**Semantic search:**
```
"Show me calls where budget wasn't discussed"
→ Searches by meaning, not just keywords → finds relevant records
  even if the word "budget" doesn't appear in the text
```

**Cross-stage comparison:**
```
"Compare BDR and AE analyses for the same deals"
→ Joins records by entity_id → shows where evaluations diverge
→ "BDRs graded 6 of 12 deals as B+, but AEs found them to be D.
   The gap is aspirational vs. confirmed budget language."
```

**Track trends:**
```
"Is lead quality getting better or worse over the last 3 weeks?"
→ Compares records by time period → identifies trajectory
```

---

## Always-On Mode (Optional)

By default, the webhook server runs while Claude Code is open. If you want data to accumulate 24/7 — even overnight or on weekends — run the standalone daemon:

```bash
clay-webhook-daemon
```

This runs the webhook receiver as a separate background process. Same database — when you open Claude Code next, all records are already there.

```bash
# Custom port and data directory
clay-webhook-daemon --port 9000 --data-dir ~/clay-data

# Run in background on Mac
nohup clay-webhook-daemon > /dev/null 2>&1 &
```

---

## Embeddings

Embeddings power semantic search — finding records by meaning, not just keywords.

| Provider | Cost | Setup |
|----------|------|-------|
| **OpenAI** (recommended) | ~$0.008 / 1,000 records | Set `OPENAI_API_KEY` in plugin config |
| **Local** | Free | `pip install clay-backend-plugin[local-embeddings]` (~80MB model) |
| **None** | — | Skip embeddings. Filters and text search still work. |

Configure via plugin settings after install. If you have an OpenAI key (most Clay users do), that's the easiest path.

---

## MCP Tools

9 tools available to Claude:

| Tool | What it does |
|------|-------------|
| `ingest_records` | Import JSON records. Deduplicates automatically. |
| `ingest_csv` | Import a CSV file with column mapping. |
| `query_records` | Filter records by type, entity, tags, date range, or text search. |
| `semantic_search` | Natural language search across records using embeddings. |
| `get_record` | Fetch a single record by ID. |
| `list_analysis_types` | Show what types of data are stored with counts. |
| `get_analytics` | Summary stats, top entities, storage size. |
| `get_webhook_url` | Get the webhook URL and Clay configuration template. |
| `delete_records` | Remove records by type, date, or ID (requires at least one filter). |

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

**Deduplication**: Records are uniquely identified by `record_id` + `analysis_type`. Sending the same combination again updates the existing record.

**Cross-record joins**: Use `entity_id` when the same deal/company/prospect is analyzed in multiple tables (e.g., BDR call analysis + AE call analysis + email sentiment). This is what enables cross-stage comparison.

---

## Example: Sales Ops Weekly Review

Sarah manages 6 BDRs. Clay runs AI analysis on each Gong call transcript.

**Setup (once):**
1. Installs plugin: `/plugin install clay-backend-plugin`
2. Adds HTTP API column to her Clay table pointing to `localhost:8742/webhook`
3. Optionally runs `clay-webhook-daemon` for 24/7 collection

**Monday morning:**
```
Sarah: "What patterns do you see in last week's calls?"

Claude: Fetches 43 records → analyzes →

  Key Findings (March 23–30):
  1. Budget qualification skipped in 65% of calls — team-wide, not one rep
  2. Bob's calls average score 34 (team avg 62) — skips discovery entirely
  3. Lead quality dropped from B to C+ — 11 of 43 were D-grade leads,
     9 came from the same webinar campaign

  Recommendations:
  • Coaching (all): Role-play budget discovery question
  • Coaching (Bob): Dedicated discovery framework session
  • Process: Investigate lead source — webinar-march-2026 is sending
    unqualified leads
```

Every one of those insights required looking across rows. Clay can't do it. This plugin can.

---

## Architecture

```
Plugin
├── .claude-plugin/plugin.json    Plugin manifest + user config
├── .mcp.json                     MCP server config (auto-starts)
├── skills/                       Guided workflows (3 skills)
├── agents/                       Synthesis subagent
└── src/clay_backend/
    ├── server.py                 MCP server (9 tools) + webhook startup
    ├── webhook_server.py         HTTP POST /webhook + GET /health
    ├── daemon.py                 Standalone webhook CLI
    ├── database.py               SQLite + sqlite-vec
    ├── models.py                 Pydantic models
    ├── services/
    │   ├── record_service.py     Store, deduplicate, query, aggregate
    │   ├── embedding_service.py  Provider factory + text extraction
    │   └── search_service.py     Vector similarity search
    └── embeddings/
        ├── base.py               Abstract provider interface
        ├── openai_provider.py    text-embedding-3-small (1536 dims)
        └── local_provider.py     all-MiniLM-L6-v2 (384 dims)

Data: ~/.claude/plugins/data/clay-backend-plugin/clay.db
Webhook: http://localhost:8742/webhook
```

---

## Hosted Mode (Optional)

Don't want to deal with ngrok? Deploy the webhook receiver as a tiny hosted service and get a permanent URL.

**One-click deploy:**

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.app/template) <!-- TODO: add template link -->

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
git clone <repo-url>
cd clay-backend-plugin
uv venv && uv pip install -e ".[local-embeddings]"

# Test with Claude Code
claude --plugin-dir .

# Lint
ruff check src/

# Run tests
uv run pytest
```

## Roadmap

- [x] MCP server with 9 tools (store, query, search, analytics)
- [x] Pluggable embeddings (OpenAI + local sentence-transformers)
- [x] Semantic search via sqlite-vec
- [x] Webhook HTTP server (receives Clay data automatically)
- [x] Standalone webhook daemon (24/7 collection)
- [x] Skills for guided import and analysis
- [x] Synthesis agent for deep cross-record analysis
- [x] Hosted backend option (FastAPI + PostgreSQL) with one-click deploy
- [ ] Embeddings support in hosted mode (pgvector)

## License

MIT

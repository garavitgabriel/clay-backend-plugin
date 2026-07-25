---
name: setup-guide
description: Help new users get started with the Clay Backend Plugin. Use when the user asks how to set up the plugin, how to export data from Clay, or needs a walkthrough of the plugin's capabilities.
allowed-tools: mcp__clay-backend__list_analysis_types mcp__clay-backend__get_analytics mcp__clay-backend__get_webhook_url mcp__clay-backend__doctor
---

# Clay Backend Plugin Setup Guide

Walk the user through getting started with the Clay Backend Plugin.

## What This Plugin Does

Clay processes data one row at a time. This plugin stores your Clay analysis results and lets you:
- **Search across records** — find all calls where budget wasn't discussed, even if those exact words aren't used
- **Find patterns** — what keeps showing up across 50 call analyses? What's the team's biggest coaching gap?
- **Compare across stages** — does the BDR's qualification hold up when the AE runs discovery?
- **Track trends** — are leads getting better or worse week over week?

## How to Export Data from Clay

### Option 1: CSV Export (Easiest)
1. Open your Clay table
2. Click the export button (top right)
3. Select "Export as CSV"
4. Save the file to your computer
5. Tell me: "Import this CSV" and provide the file path

### Option 2: Copy JSON from Clay
1. In Clay, add an "AI Analysis" column that outputs structured JSON
2. Copy the JSON output for the records you want to analyze
3. Paste it here and tell me to import it

### Option 3: Live Webhooks (Recommended for ongoing use)
The plugin runs a webhook server that Clay can POST to automatically.

1. Ask me for the webhook URL (I'll call `get_webhook_url`)
2. In Clay, add an "HTTP API" enrichment column to your table
3. Set Method to **POST**
4. Set the URL to the webhook URL (default: `http://localhost:8742/webhook`)
5. Set the body to a JSON template mapping your columns:
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
6. Run your table — each row will automatically be sent to the plugin as it's processed

**Note**: By default, the webhook server runs while Claude Code is open. For 24/7 collection (even when Claude Code is closed), see "Always-On Mode" below.

**Recommended body pattern for anything non-trivial.** Merge tokens containing quotes or newlines corrupt hand-written JSON, and array columns arrive as strings rather than arrays — both silently. Instead, have the user add a Clay **formula column** that builds the object:

```javascript
JSON.stringify({
  record_id: {{Row ID}},
  analysis_type: "call_analysis",
  data: { summary: {{AI Summary}}, emails: {{Generated Emails}} }
})
```

Then in the HTTP API column, set the whole body to `Clay.formatForJSON({{Webhook Payload}})`. Arrays stay arrays, escaping is handled, and the payload is inspectable before it's sent.

### Exposing to Clay Cloud (tunnel)

Clay runs in the cloud, so it can't reach `localhost` directly. Either tunnel works:

- **cloudflared** — no account needed: `brew install cloudflared` then `cloudflared tunnel --url http://127.0.0.1:8742`
- **ngrok** — free account required: `brew install ngrok`, `ngrok config add-authtoken <token>`, then `ngrok http 8742`

Both print a public HTTPS URL. Use it plus `/webhook` in Clay. On free tiers the URL changes when the tunnel restarts, and the Clay column has to be updated.

**Verify the tunnel before the user touches Clay** — it splits three failure modes apart:

```bash
curl https://<tunnel-host>/health          # tunnel reachable?
curl -X POST https://<tunnel-host>/webhook \
  -H "Authorization: Bearer <key>" -H "Content-Type: application/json" \
  -d '{"record_id":"smoke-1","analysis_type":"smoke_test","data":{"hello":"world"}}'
```

If the first fails it's the tunnel; if the second returns 401 it's the key; if both pass, anything later is the Clay column config. Offer to delete the `smoke_test` records afterwards.

**Important**: When exposing via a tunnel, set `WEBHOOK_API_KEY` in the plugin config to protect the endpoint. Then in Clay's HTTP API column, add a header: `Authorization: Bearer <your-key>`. Call `get_webhook_url` to see the exact header to use.

## Troubleshooting

If anything looks wrong — "0 records" after ingesting, semantic search failing, the plugin not connecting — call the `doctor` tool first. It checks SQLite extension support, the resolved database path and record counts, webhook port ownership, the embedding provider, and webhook auth, and prints the fix for each. It resolves most setup problems in one step, so reach for it before debugging by hand.

Two things it catches that are otherwise invisible:

- **Split-brain database.** The plugin and the standalone `clay-webhook-daemon` both default to `~/.clay-backend`, but if either was pointed elsewhere they write to different `clay.db` files and records appear to vanish. `doctor` compares the paths.
- **Semantic search unavailable.** pyenv-built Pythons and Apple's `/usr/bin/python3` can't load SQLite extensions. Everything else works; only `semantic_search` is affected. The fix is a venv on a uv-managed Python.

## After Import

Once your data is imported, you can:

1. **Ask about patterns**: "What are the common issues across my call analyses?"
2. **Search semantically**: "Find records where the rep skipped discovery"
3. **Compare entities**: "Show me all analyses for deal X"
4. **Get analytics**: "How many records do I have by type?"

## Embedding Configuration

**Don't push embeddings on a new user.** Below roughly 1,000 records, filters plus text search answer essentially everything, and configuring embeddings is the single biggest source of setup friction. Start with none; suggest turning them on when the user's record count grows or when they ask a question that keyword search genuinely can't answer (same concept, different wording across records).

When they're ready:

- **None** (start here): Skip embeddings entirely. Filters and text search still work.

- **OpenAI**: Fast, lightweight, costs ~$0.008 per 1000 records. Set `EMBEDDING_PROVIDER=openai` and provide your `OPENAI_API_KEY` in plugin config.

- **Local model**: Free, runs on your machine. Set `EMBEDDING_PROVIDER=local` in plugin config.
  Requires: `uv pip install -e ".[local-embeddings]"` (downloads ~80MB model, needs ~2GB with PyTorch)

Switching providers later means re-embedding everything — the vector dimension is fixed at first ingest — so if the user knows they'll want OpenAI eventually, starting there avoids a migration.

Note that semantic search also needs a Python built with SQLite extension support (see Troubleshooting).

## Always-On Mode (Optional)

By default, the webhook server only runs while Claude Code is open. If you want data to accumulate 24/7 (e.g., calls happening overnight), run the standalone daemon:

```bash
clay-webhook-daemon
```

This runs the webhook receiver independently of Claude Code. Data is stored in the same database — when you open Claude Code, everything is already there.

Options:
- `--port 9000` — custom port (default: 8742)
- `--data-dir ~/clay-data` — custom data directory

To run it in the background on Mac, add it as a login item or use:
```bash
nohup clay-webhook-daemon > /dev/null 2>&1 &
```

## Check Current Status

Let me check what data you have stored right now.

Call `list_analysis_types` and `get_analytics` to show the user their current state. If empty, guide them through their first import.

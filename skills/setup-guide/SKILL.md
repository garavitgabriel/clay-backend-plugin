---
name: setup-guide
description: Help new users get started with the Clay Backend Plugin. Use when the user asks how to set up the plugin, how to export data from Clay, or needs a walkthrough of the plugin's capabilities.
allowed-tools: mcp__clay-backend__list_analysis_types mcp__clay-backend__get_analytics mcp__clay-backend__get_webhook_url
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

### Exposing to Clay Cloud (ngrok)

Clay runs in the cloud, so it can't reach `localhost` directly. Use ngrok to create a secure tunnel:

1. Install ngrok: `brew install ngrok` (Mac) or download from ngrok.com
2. Sign up for a free ngrok account and run `ngrok config add-authtoken <your-token>`
3. Start the tunnel: `ngrok http 8742`
4. Copy the ngrok URL (e.g., `https://abc123.ngrok-free.app`)
5. In Clay, use `https://abc123.ngrok-free.app/webhook` as the URL

**Important**: When exposing via ngrok, set `WEBHOOK_API_KEY` in the plugin config to protect the endpoint. Then in Clay's HTTP API column, add a header: `Authorization: Bearer <your-key>`. Call `get_webhook_url` to see the exact header to use.

## After Import

Once your data is imported, you can:

1. **Ask about patterns**: "What are the common issues across my call analyses?"
2. **Search semantically**: "Find records where the rep skipped discovery"
3. **Compare entities**: "Show me all analyses for deal X"
4. **Get analytics**: "How many records do I have by type?"

## Embedding Configuration

The plugin can generate embeddings for semantic search. Options:

- **Local model** (default): Free, runs on your machine. Set `EMBEDDING_PROVIDER=local` in plugin config.
  Requires: `pip install clay-backend-plugin[local-embeddings]` (downloads ~80MB model, needs ~2GB with PyTorch)

- **OpenAI**: Fast, lightweight, costs ~$0.008 per 1000 records. Set `EMBEDDING_PROVIDER=openai` and provide your `OPENAI_API_KEY` in plugin config.

- **None**: Skip embeddings entirely. You can still query records by filters and text search, just not semantic search.

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

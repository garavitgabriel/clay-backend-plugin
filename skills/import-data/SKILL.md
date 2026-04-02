---
name: import-data
description: Import Clay data into the backend plugin. Use when the user wants to import CSV exports, paste JSON records, or load data from Clay tables into the plugin for analysis.
allowed-tools: Read Glob mcp__clay-backend__ingest_records mcp__clay-backend__ingest_csv mcp__clay-backend__list_analysis_types mcp__clay-backend__get_analytics
---

# Import Clay Data

Help the user import their Clay data into the Clay Backend Plugin for cross-record analysis.

## Step 1: Determine the data format

Ask the user what they have:
- **CSV file** — a Clay table export (most common)
- **JSON records** — copied from Clay's AI analysis output or API
- **Pasted data** — raw text they want you to structure

## Step 2: For CSV imports

1. Ask for the file path (or have them drag the file into the chat)
2. Read the first few lines to understand the columns
3. Ask the user:
   - Which column contains the **unique row ID** (e.g., "Row ID", "Record ID")
   - What **analysis type** name to use (e.g., "call_analysis", "lead_scoring")
   - Which columns contain the **analysis data** to store
   - Optionally: which column is the **entity ID** (for cross-record joins, e.g., "Deal ID", "Company Domain")
   - Optionally: which column is the **entity name** (human-readable label)
4. Call the `ingest_csv` tool with the mapping
5. Report results

## Step 3: For JSON imports

1. Have the user paste their JSON or point to a file
2. Ensure each record has at minimum: `record_id`, `analysis_type`, and `data`
3. Help structure the data if needed — the `data` field is flexible JSON
4. Call the `ingest_records` tool
5. Report results

## Step 4: Confirm and suggest next steps

After import, always:
1. Call `list_analysis_types` to show what's stored
2. Call `get_analytics` for a summary
3. Suggest what they can do next:
   - "Ask me to find patterns across your [analysis_type] records"
   - "Search for specific topics: 'show me records about budget objections'"
   - "Compare records by entity: 'how do analyses differ for deal X vs deal Y'"

## Tips

- If the user has multiple Clay tables (e.g., call analyses AND email analyses), import each with a different `analysis_type` value
- If records share an entity (like a deal or company), use `entity_id` so cross-record joins work later
- The `embed_fields` parameter controls which data fields get embedded for semantic search. If omitted, all fields are embedded.

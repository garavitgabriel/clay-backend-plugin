---
name: analyze-patterns
description: Find patterns, trends, and insights across stored Clay analysis records. Use when the user asks about patterns, trends, comparisons, common issues, coaching priorities, or wants a summary across multiple records.
allowed-tools: mcp__clay-backend__query_records mcp__clay-backend__semantic_search mcp__clay-backend__list_analysis_types mcp__clay-backend__get_analytics mcp__clay-backend__get_record
---

# Analyze Patterns Across Clay Data

Synthesize insights from stored Clay analysis records. You ARE the synthesis engine — query the data and reason over it directly.

## Step 1: Understand what's available

Call `list_analysis_types` to see what data is stored. Share the summary with the user so they know what's available.

## Step 2: Fetch relevant records

Based on the user's question, decide the best approach:

- **Broad pattern analysis**: Use `query_records` with the relevant `analysis_type`, fetch up to 50-100 records
- **Topic-specific search**: Use `semantic_search` to find records matching a specific theme (e.g., "budget objections", "competitor mentions")
- **Entity comparison**: Use `query_records` filtered by `entity_id` to compare analyses of the same deal/company across stages
- **Time-based trends**: Use `since`/`until` filters to compare different periods

## Step 3: Synthesize

When analyzing the returned records, look for:

1. **Recurring patterns** — what keeps showing up across multiple records? Don't just average scores — identify specific behaviors or themes that repeat
2. **Outliers** — which records deviate significantly? What makes them different?
3. **Trends** — are things getting better or worse over time? Compare earlier vs recent records
4. **Segments** — do patterns differ by rep, entity, source, or tag? Break down by dimensions
5. **Root causes** — separate individual issues from systemic ones. "One rep's problem" vs "team-wide gap" vs "process issue"
6. **Actionable recommendations** — what should change? Be specific: cite record IDs and data as evidence

## Step 4: Format findings

Structure your analysis as:

### Key Findings
- Top 3-5 patterns with specific evidence (cite record IDs)

### Breakdowns
- Per-segment analysis if applicable (by rep, entity, tag)

### Recommendations
- Specific, actionable next steps
- Attribute each recommendation to the right owner (coaching, process, tooling)

## Cross-Stage Analysis

When the user asks to compare analyses across stages (e.g., BDR vs AE assessment of the same deal):

1. Fetch records with the same `entity_id` but different `analysis_type` values
2. Compare the assessments side by side
3. Identify where evaluations diverge — this reveals handoff gaps
4. Note: "The BDR scored this B but the AE found it was a D" is a high-value insight

## Important

- Always cite specific records as evidence — don't make generic claims
- If there are too many records to process at once, work in batches and synthesize across batches
- If the user hasn't imported data yet, suggest using the import-data skill first

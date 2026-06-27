#!/usr/bin/env python
"""Full local-loop smoke test — no Claude Code, no MCP layer in the way.

  1. start the real webhook HTTP receiver (uvicorn thread)
  2. POST several fixture records to it over the network
  3. query them back through the service layer
  4. run analytics + a semantic_search and print results
  5. if ANTHROPIC_API_KEY is set, run ONE real scheduler synthesis; else skip

Exits 0 on success. Run:  uv run python tests/smoke_local.py
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

# --- env must be set BEFORE importing clay_backend (cached globals) ----------
_TMP = tempfile.mkdtemp(prefix="clay_smoke_")
os.environ["CLAY_DATA_DIR"] = _TMP
os.environ.setdefault("EMBEDDING_PROVIDER", "local")
os.environ.pop("REMOTE_URL", None)

import httpx  # noqa: E402

from clay_backend.database import init_db  # noqa: E402
from clay_backend.services import record_service, search_service  # noqa: E402
from clay_backend.webhook_server import start_webhook_server  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_for_health(base: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base}/health", timeout=1.0)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise RuntimeError("webhook server did not come up in time")


def main() -> int:
    print(f"== clay-backend local smoke ==\ndata dir: {_TMP}\n")
    init_db()

    records = json.loads((FIXTURES / "clay_records.json").read_text())
    for r in records:
        r.pop("_created_at", None)

    # 1. start webhook receiver -------------------------------------------------
    port = _free_port()
    os.environ["WEBHOOK_PORT"] = str(port)
    start_webhook_server(port=port)
    base = f"http://127.0.0.1:{port}"
    _wait_for_health(base)
    print(f"[1] webhook server up on {base}")

    # 2. POST a batch of fixture records over the network ----------------------
    sample = records[:30]  # mix of both analysis types
    resp = httpx.post(f"{base}/webhook", json={"records": sample}, timeout=60)
    resp.raise_for_status()
    body = resp.json()
    print(f"[2] POST /webhook -> ingested={body['ingested']} updated={body['updated']} "
          f"errors={len(body['errors'])}")
    assert body["ingested"] == 30, body

    # Re-POST 5 of them to prove dedup over the wire (update, not duplicate).
    resp2 = httpx.post(f"{base}/webhook", json={"records": sample[:5]}, timeout=60)
    b2 = resp2.json()
    print(f"    re-POST 5 -> ingested={b2['ingested']} updated={b2['updated']} (dedup)")
    assert b2["updated"] == 5 and b2["ingested"] == 0, b2

    # 3. query back through the service layer ----------------------------------
    calls = record_service.query_records(analysis_type="call_analysis", limit=200)
    enrich = record_service.query_records(analysis_type="company_enrichment", limit=200)
    print(f"[3] query_records: call_analysis={len(calls)} company_enrichment={len(enrich)}")
    assert len(calls) + len(enrich) == 30

    # 4. analytics + semantic search -------------------------------------------
    a = record_service.get_analytics()
    print(f"[4] analytics: total={a.total_records} types="
          f"{[(t.analysis_type, t.count) for t in a.records_by_type]} "
          f"size={a.storage_size_mb}MB top_entities={len(a.top_entities)}")

    hits = search_service.semantic_search(
        "calls where the prospect went quiet and there was ghosting risk", top_k=3
    )
    print("    semantic_search top 3:")
    for h in hits:
        if "error" in h:
            print(f"      ERROR: {h['error']}")
            return 1
        rec = h["record"]
        summ = str(rec["data"].get("summary", ""))[:70]
        print(f"      {h['similarity_score']:.4f}  {rec['record_id']:>10}  {summ}…")

    # 5. optional real scheduler synthesis -------------------------------------
    if os.environ.get("ANTHROPIC_API_KEY"):
        from clay_backend import scheduler

        print("\n[5] ANTHROPIC_API_KEY set -> running ONE real scheduler synthesis")
        try:
            skill_prompt = scheduler._load_skill("analyze-patterns")
        except FileNotFoundError:
            skill_prompt = "Summarize the key cross-record patterns in 3 bullets."
        recs = scheduler._fetch_records({"analysis_type": "call_analysis"})
        out = scheduler._call_anthropic(
            skill_prompt, recs, {"name": "smoke", "context": {"analysis_type": "call_analysis"}}
        )
        print("    --- synthesis ---")
        print("    " + out.strip().replace("\n", "\n    ")[:1200])
    else:
        print("\n[5] ANTHROPIC_API_KEY not set -> skipping real synthesis (ok)")

    print("\n== SMOKE OK ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())

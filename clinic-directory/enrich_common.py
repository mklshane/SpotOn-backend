"""Shared helpers for the directory-enrichment scripts (05-08).

Single source of truth for the controlled vocabularies, the Supabase client,
rate-limiting, enrichment_meta merging, and the run report. Mirrors the env /
client setup used by the sibling collectors (02-collect.py, 04-pds.py).
"""
from __future__ import annotations

import json
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from supabase import Client, ClientOptions, create_client

load_dotenv()

# --- Controlled vocabularies (mirror api/app/core/vocab.py SERVICES) ----------
SERVICES = [
    "dermoscopy", "skin_biopsy", "excision", "mohs_surgery", "cryotherapy",
    "electrosurgery", "curettage", "histopathology", "immunohistochemistry",
    "total_body_photography", "teledermatology", "oncology_treatment",
]
SERVICES_SET = set(SERVICES)

FACILITY_TYPES = ["medical", "aesthetic", "mixed", "unknown"]

# The status value used to hide a clinic from the directory (Phase 4). The API
# (api/app/routers/directory.py) excludes this value by default.
EXCLUDED_STATUS = "excluded"

RAW_DIR = Path("./data/raw")


def make_client() -> Client:
    """Create a Supabase client from .env (service-role key), like 02-collect.py."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise SystemExit("ERROR: SUPABASE_URL / SUPABASE_SERVICE_KEY missing from .env")
    options = ClientOptions(postgrest_client_timeout=30.0)
    return create_client(url, key, options=options)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_all(supabase, table: str, cols: str, order_col: str = "id",
              page: int = 1000) -> list[dict]:
    """Fetch every row of a table, paginating past PostgREST's 1000-row cap.

    Without this, supabase.table(...).select(...).execute() silently returns only
    the first 1000 rows — so any table larger than that would be partly skipped.
    Orders by a stable unique column (id) so range pagination can't drop/duplicate.
    """
    rows: list[dict] = []
    start = 0
    while True:
        chunk = (
            supabase.table(table).select(cols).order(order_col)
            .range(start, start + page - 1).execute().data
        ) or []
        rows.extend(chunk)
        if len(chunk) < page:
            break
        start += page
    return rows


def rate_limit_sleep(base: float = 3.0, jitter: float = 1.0) -> None:
    """Polite delay between records; randomised so we don't hammer in lockstep."""
    time.sleep(base + random.uniform(0, jitter))


def merge_meta(existing: dict | None, updates: dict) -> dict:
    """Shallow-merge enrichment_meta so a later phase never clobbers an earlier
    one's keys (e.g. Phase 3 'filled' must not drop Phase 2 'classification').

    `sources` / `queries` lists are unioned; nested dicts (classification,
    filled) are merged one level deep.
    """
    out = dict(existing or {})
    for k, v in updates.items():
        if k in ("sources", "queries") and isinstance(v, list):
            prev = out.get(k) or []
            out[k] = list(dict.fromkeys([*prev, *v]))  # union, order-preserving
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            merged = dict(out[k])
            merged.update(v)
            out[k] = merged
        else:
            out[k] = v
    return out


def is_blank(value) -> bool:
    """True if a field should be treated as empty / fillable."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    if isinstance(value, (list, tuple)) and len(value) == 0:
        return True
    return False


def valid_url(value) -> bool:
    """True only for a real, usable http(s) destination URL.

    Guards against (a) prose descriptions (e.g. 'Makati Medical Center website') and
    (b) Google Search grounding *redirect* URLs (vertexaisearch.cloud.google.com/
    grounding-api-redirect/...) — those are provenance pointers, not real destinations,
    and must never be stored in a website/booking_url field.
    """
    if not isinstance(value, str):
        return False
    v = value.strip().lower()
    if "vertexaisearch.cloud.google.com" in v or "grounding-api-redirect" in v:
        return False
    return (v.startswith("http://") or v.startswith("https://")) and "." in v and " " not in v


def clean_services(values) -> list[str]:
    """Keep only valid, de-duplicated SERVICES vocab tags (order-preserving)."""
    if not values:
        return []
    seen: list[str] = []
    for v in values:
        s = getattr(v, "value", v)  # accept enum members or strings
        if s in SERVICES_SET and s not in seen:
            seen.append(s)
    return seen


def append_audit(record: dict, name: str = "enrichment_audit.jsonl") -> None:
    """Append one JSON line of raw LLM provenance to data/raw/<name>.

    One line per facility: the raw Gemini research text, the search queries, the
    grounding sources, and the extracted result — keyed by facility id so it joins
    back to the row. Append-only (survives reruns) and never raises.
    """
    try:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        with open(RAW_DIR / name, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception as e:  # auditing must never crash a run
        print(f"  (audit write failed: {e})")


def write_report(report: dict, name: str) -> Path | None:
    """Write the run report JSON to data/raw/. Returns the path (or None)."""
    try:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        path = RAW_DIR / name
        path.write_text(json.dumps(report, indent=2, default=str))
        return path
    except Exception as e:  # reporting must never crash a run
        print(f"  (could not write report: {e})")
        return None

# Directory Enrichment (Gemini + Google Search grounding)

A data-side job that enriches the SpotOn `facilities` / `doctors` tables in
Supabase. It uses the Gemini API with Google Search grounding to (1) classify
facilities and soft-flag aesthetic-only clinics, and (2) fill blank fields from
real, cited web sources. It writes directly to Supabase, like the sibling
collectors (`02-collect.py`, `03_scrape_bookaderma.py`, `04-pds.py`).

This is a **medical** directory. The whole design keeps the LLM's output
**reversible, grounded, and human-gated**:
- Classification is advisory and **never changes `status`**.
- Blanks-only writes — collector/human data is never overwritten.
- **Null is the correct answer** when the web is silent.
- Hiding a clinic is a separate, confidence-thresholded, `--dry-run`-by-default,
  human-approved step.

## Setup

```bash
# from repo root (SpotOn/app)
source .venv/bin/activate
pip install -r SpotOn-backend/clinic-directory/requirements.txt   # google-genai, tenacity, ...
```

`.env` (in `clinic-directory/`, gitignored) needs — see `.env.example`:

```
SUPABASE_URL=...
SUPABASE_SERVICE_KEY=...
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.5-flash      # bump to a Gemini 3 model later; or gemini-flash-latest
```

> **Billing:** grounded search is billed per prompt (2.5) / per query (3). A
> `429 RESOURCE_EXHAUSTED — prepayment credits depleted` means the Gemini project
> is out of credits — top up at https://ai.studio/projects. The two-phase design
> makes ~2 calls per record, so a full run is bounded; checkpoints + resumability
> stop a re-run from re-billing finished work.

## Run order

Run every script `--dry-run` (the default) first, inspect the output, then
`--apply`. All scripts take `--limit N` for a small test batch and `--force` to
redo already-processed rows.

| # | Script | What it does |
|---|--------|--------------|
| 0 | `migrations/006_directory_enrichment.sql` | **Human runs in Supabase.** Adds enrichment columns. ⛔ stop & verify columns exist first. |
| 1 | `gemini_client.py` | Smoke test: `python gemini_client.py` — grounded research + schema extraction. |
| 2 | `05_classify_facilities.py` | Classify medical/aesthetic/mixed/unknown; flag aesthetic-only; **no status change**. ⛔ human review after. |
| 3 | `06_fill_facilities.py` | Fill blank `services`/`phone`/`website`/`address`/`has_philhealth` with provenance. |
| 4 | `07_exclude_facilities.py` | Set `status='excluded'` on high-confidence aesthetic-only clinics (approved set only). |
| 5 | `08_enrich_doctors.py` | Light-touch: fill blank doctor `website` / affiliation from authoritative sources. |

Typical batch flow for each phase:

```bash
cd SpotOn-backend/clinic-directory
python 05_classify_facilities.py --dry-run --limit 15   # inspect
python 05_classify_facilities.py --apply  --limit 15    # write a batch, spot-check Supabase
python 05_classify_facilities.py --apply                # full run (resumable)
```

## Two-phase pipeline (why)

Gemini 2.5 can't combine `google_search` grounding with a JSON `response_schema`
in one call. So each record is two calls (`gemini_client.py`):
- **A — `research(prompt)`**: grounded; returns prose + source URIs + the search
  queries the model ran.
- **B — `extract(notes, schema)`**: no tools; strict pydantic `response_schema`;
  extracts A's notes into JSON, returning `null` where unsupported. `services`
  is enum-constrained to the 12-value vocab.

Provenance (source URIs, queries, and per-field `{value, source_url}`) is stored
in `facilities.enrichment_meta` / `doctors.enrichment_meta`.

## Reversing an exclusion

`07_exclude_facilities.py` only sets `status='excluded'` — the row is never
deleted. The directory API (`api/app/routers/directory.py`) hides that status by
default but still returns it for `?status=excluded`. To restore one:

```sql
update facilities set status = null where id = '<facility-uuid>';
```

The pre-exclusion status is also saved at
`enrichment_meta.excluded.previous_status`.

## Resumability & reports

- Each phase skips already-done rows via markers in `enrichment_meta`
  (`classification`, `filled`, `light_touch`); `--force` overrides.
- Retries with exponential backoff on 429 / transient errors (tenacity); a
  randomised rate-limit sleep runs between records.
- Each run writes a summary to `data/raw/enrichment_report_<phase>.json`.

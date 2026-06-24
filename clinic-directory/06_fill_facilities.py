"""Phase 3 — Fill blank facility fields from grounded web sources.

Blanks-only: writes a field ONLY if it is currently empty AND the model returned a
non-null, grounded value. Never overwrites collector/human data. Every written
value is recorded with its source URL under enrichment_meta.filled. `status` is
left un-verified (the existing human verification step promotes the data).

  python 06_fill_facilities.py --dry-run --limit 15   # default: prints diffs, no writes
  python 06_fill_facilities.py --apply --limit 15
  python 06_fill_facilities.py --apply                # full run
  python 06_fill_facilities.py --apply --force        # re-fill already-filled rows

Target fields: services (empty []), phone, website, address, has_philhealth (null).
"""
from __future__ import annotations

import argparse
import sys
import traceback

from enrich_common import (
    SERVICES,
    clean_services,
    fetch_all,
    is_blank,
    make_client,
    merge_meta,
    now_iso,
    rate_limit_sleep,
    valid_url,
    write_report,
)
from enrich_schemas import FacilityEnrichment
from gemini_client import MODEL, GeminiQuotaError, extract, research

# Scalar fields we may fill (services handled separately as a list).
SCALAR_TARGETS = ["phone", "website", "address", "booking_url", "has_philhealth"]
URL_FIELDS = {"website", "booking_url"}  # must be a real URL, not prose


def research_prompt(fac: dict, blanks: list[str]) -> str:
    bits = [f"Name: {fac.get('name','')}"]
    for k in ("city", "province", "region"):
        if fac.get(k):
            bits.append(f"{k.title()}: {fac[k]}")
    if fac.get("website"):
        bits.append(f"Known website: {fac['website']}")
    ident = "\n".join(bits)
    wanted = ", ".join(blanks)
    return (
        "You are gathering verified contact and service details for a clinic/"
        "facility in the Philippines, for a skin-cancer directory. Use web search "
        "and report ONLY facts you find in real sources.\n\n"
        f"{ident}\n\n"
        f"Find these missing details if — and only if — sources clearly support "
        f"them: {wanted}.\n"
        "- phone: the facility's official contact number.\n"
        "- website: official website URL.\n"
        "- address: full street address.\n"
        "- booking_url: URL to this clinic's OWN online appointment-booking page, if "
        "one genuinely exists (most clinics book by phone — return null then).\n"
        "- has_philhealth: whether the facility accepts PhilHealth (true/false).\n"
        "- services: which of the following skin-cancer-relevant services it "
        f"offers (use ONLY these exact terms): {', '.join(SERVICES)}.\n\n"
        "If you cannot find a fact in a real source, say it is unknown. Do NOT "
        "guess. Quote or name the source for each fact."
    )


def fill_one(fac: dict, blanks: list[str]) -> dict:
    res = research(research_prompt(fac, blanks))
    obj: FacilityEnrichment = extract(res["text"], FacilityEnrichment)
    source = res["sources"][0] if res["sources"] else None

    proposed: dict = {}
    if "services" in blanks:
        svc = clean_services(obj.services)
        if svc:
            proposed["services"] = svc
    for f in SCALAR_TARGETS:
        if f in blanks and not is_blank(getattr(obj, f)):
            val = getattr(obj, f)
            # website / booking_url must be a real URL, not a prose description.
            if f in URL_FIELDS and not valid_url(val):
                continue
            proposed[f] = val

    return {"proposed": proposed, "source": source,
            "sources": res["sources"], "queries": res["queries"]}


def fetch_facilities(supabase, limit: int | None, force: bool) -> list[dict]:
    cols = ("id,name,city,province,region,website,phone,address,services,"
            "booking_url,has_philhealth,enrichment_meta")
    rows = fetch_all(supabase, "facilities", cols)
    out = []
    for r in rows:
        meta = r.get("enrichment_meta")
        already_filled = isinstance(meta, dict) and meta.get("filled")
        if already_filled and not force:
            continue
        blanks = compute_blanks(r)
        if not blanks:
            continue
        r["_blanks"] = blanks
        out.append(r)
    if limit:
        out = out[:limit]
    return out


def compute_blanks(fac: dict) -> list[str]:
    blanks = []
    if is_blank(fac.get("services")):
        blanks.append("services")
    for f in SCALAR_TARGETS:
        if is_blank(fac.get(f)):
            blanks.append(f)
    return blanks


def main() -> None:
    ap = argparse.ArgumentParser(description="Fill blank facility fields (blanks only).")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="Print diffs only (default).")
    g.add_argument("--apply", action="store_true", help="Write filled fields to Supabase.")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true", help="Re-fill already-filled rows.")
    args = ap.parse_args()
    apply = args.apply

    supabase = make_client()
    rows = fetch_facilities(supabase, args.limit, args.force)

    print("=" * 78)
    print(f"PHASE 3 — FILL BLANK FACILITY FIELDS  [{'APPLY' if apply else 'DRY-RUN'}]  model={MODEL}")
    print(f"Facilities with blanks to process: {len(rows)}")
    print("=" * 78)

    stats = {"processed": 0, "fields_written": 0, "rows_written": 0,
             "errored": 0, "no_data": 0}
    per_field = {f: 0 for f in ["services", *SCALAR_TARGETS]}

    for i, fac in enumerate(rows, 1):
        name = fac.get("name", "?")
        blanks = fac["_blanks"]
        try:
            r = fill_one(fac, blanks)
        except GeminiQuotaError as e:
            print(f"\n{e}")
            print("Aborting run — no further calls made. Re-run when on a paid tier.")
            break
        except Exception as e:
            stats["errored"] += 1
            print(f"[{i}/{len(rows)}] ERROR {name[:40]}: {e}")
            traceback.print_exc(file=sys.stdout)
            rate_limit_sleep()
            continue

        stats["processed"] += 1
        proposed = r["proposed"]
        print(f"[{i}/{len(rows)}] {name[:42]:42} | blanks: {','.join(blanks)}")
        if not proposed:
            stats["no_data"] += 1
            print("            -> no grounded values found (left blank — correct).")
            rate_limit_sleep()
            continue

        for f, v in proposed.items():
            per_field[f] += 1
            src = r["source"] or "(no source uri)"
            print(f"            {f}: <blank> -> {v}   [{src[:60]}]")

        if apply:
            filled_meta = {
                f: {"value": v, "source_url": r["source"]} for f, v in proposed.items()
            }
            meta = merge_meta(fac.get("enrichment_meta"), {
                "filled": filled_meta,
                "sources": r["sources"],
                "queries": r["queries"],
            })
            update = dict(proposed)
            update["enriched_by"] = MODEL
            update["enriched_at"] = now_iso()
            update["enrichment_meta"] = meta
            # status deliberately left un-verified.
            try:
                supabase.table("facilities").update(update).eq("id", fac["id"]).execute()
                stats["rows_written"] += 1
                stats["fields_written"] += len(proposed)
            except Exception as e:
                stats["errored"] += 1
                print(f"            DB ERROR: {e}")

        rate_limit_sleep()

    print("\n" + "=" * 78)
    print("SUMMARY")
    print(f"  processed       : {stats['processed']}")
    print(f"  rows written    : {stats['rows_written']}")
    print(f"  fields written  : {stats['fields_written']}")
    print(f"  rows w/ no data : {stats['no_data']}")
    print(f"  errored         : {stats['errored']}")
    print("  per-field (proposed):")
    for f, n in per_field.items():
        print(f"     {f:16}: {n}")
    if not apply:
        print("\n  DRY-RUN — nothing written. Re-run with --apply to persist.")
    write_report({"phase": "fill", "apply": apply, "stats": stats,
                  "per_field": per_field}, "enrichment_report_fill.json")


if __name__ == "__main__":
    main()

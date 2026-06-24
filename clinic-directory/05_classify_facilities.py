"""Phase 2 — Classify facilities AND fill blank fields, in one grounded pass.

A single grounded research call per facility does both jobs (the research already
surfaces the services, so we don't pay for it twice):
  1. CLASSIFY: facility_type (medical/aesthetic/mixed/unknown), is_aesthetic_only,
     confidence, reason, needs_review.
  2. FILL BLANKS ONLY: services, phone, website, address, has_philhealth — written
     only when currently empty AND grounded, with per-field provenance. Existing
     collector/human values are never overwritten.

NEVER changes `status` — hiding a clinic happens only in 07_exclude_facilities.py,
after a human reviews these classifications.

  python 05_classify_facilities.py --dry-run --limit 15   # default: prints, no writes
  python 05_classify_facilities.py --apply --limit 15
  python 05_classify_facilities.py --apply                # full run (resumable)
  python 05_classify_facilities.py --apply --force        # re-process already-done rows

Bias toward KEEPING: only `aesthetic` (purely cosmetic) is flagged is_aesthetic_only.
mixed/unknown are kept; unknown and low-confidence rows get needs_review=true.
"""
from __future__ import annotations

import argparse
import sys
import traceback

from enrich_common import (
    SERVICES,
    append_audit,
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

CONF_REVIEW_THRESHOLD = 0.7  # below this (or unknown) -> needs_review

# Fillable scalar fields (services is a list, handled separately).
SCALAR_TARGETS = ["phone", "website", "address", "booking_url", "has_philhealth"]

# Scalar fields that must be a real URL, not prose.
URL_FIELDS = {"website", "booking_url"}

FACILITY_TYPE_DEFS = """
facility_type definitions:
- "medical": provides medical dermatology / skin lesion evaluation / biopsy /
  excision / skin-cancer-relevant care.
- "aesthetic": PURELY cosmetic (botox, fillers, laser hair removal, facials,
  slimming, whitening) with NO medical dermatology.
- "mixed": does BOTH medical and cosmetic (very common for Philippine "derma
  clinics"). These are KEPT in the directory.
- "unknown": insufficient grounded evidence to decide. KEPT, flagged for review.
is_aesthetic_only must be true ONLY when facility_type == "aesthetic".
"""


def compute_blanks(fac: dict) -> list[str]:
    """Which target fields are currently empty (and therefore fillable)."""
    blanks = []
    if is_blank(fac.get("services")):
        blanks.append("services")
    for f in SCALAR_TARGETS:
        if is_blank(fac.get(f)):
            blanks.append(f)
    return blanks


def research_prompt(fac: dict) -> str:
    bits = [f"Name: {fac.get('name','')}"]
    for k in ("city", "province", "region"):
        if fac.get(k):
            bits.append(f"{k.title()}: {fac[k]}")
    if fac.get("website"):
        bits.append(f"Known website: {fac['website']}")
    ident = "\n".join(bits)
    return (
        "You are researching a clinic/facility in the Philippines for a SKIN CANCER "
        "directory. Using web search, find what this facility actually does AND its "
        "key public details. Report ONLY facts supported by real sources; name the "
        "source for each fact. If the web is thin or a fact is unstated, say so — do "
        "NOT guess. Null/unknown is the correct answer when sources are silent.\n\n"
        f"{ident}\n\n"
        "PART 1 — Classification. Does it provide MEDICAL dermatology (skin lesion "
        "evaluation, dermoscopy, skin biopsy, excision, cryotherapy, skin-cancer "
        "diagnosis/treatment)? Or is it PURELY cosmetic/aesthetic (facials, botox, "
        "fillers, laser hair removal, slimming, whitening) with no medical "
        "dermatology? Many Philippine 'derma clinics' do BOTH (mixed).\n"
        f"{FACILITY_TYPE_DEFS}\n"
        "PART 2 — Services & contact details. From real sources only:\n"
        "- Which of these skin-cancer-relevant services it offers (use ONLY these "
        f"exact terms): {', '.join(SERVICES)}.\n"
        "- phone: official contact number.\n"
        "- website: official website URL.\n"
        "- address: full street address.\n"
        "- booking_url: a URL to this clinic's OWN online appointment-booking page "
        "(e.g. a 'Book Now' link or booking platform). Only if one genuinely exists "
        "for this facility — most clinics book by phone and have none; return null then.\n"
        "- has_philhealth: whether it accepts PhilHealth (true/false).\n"
    )


def process_one(fac: dict, blanks: list[str]) -> dict:
    """One grounded pass: classify + propose blanks-only fills for this facility."""
    res = research(research_prompt(fac))
    obj: FacilityEnrichment = extract(res["text"], FacilityEnrichment)

    ftype = obj.facility_type.value
    is_aesthetic_only = (ftype == "aesthetic")  # enforce the invariant
    confidence = max(0.0, min(1.0, float(obj.confidence)))
    needs_review = ftype == "unknown" or confidence < CONF_REVIEW_THRESHOLD

    # Blanks-only fill proposals.
    proposed: dict = {}
    if "services" in blanks:
        svc = clean_services(obj.services)
        if svc:
            proposed["services"] = svc
    for f in SCALAR_TARGETS:
        if f in blanks and not is_blank(getattr(obj, f)):
            val = getattr(obj, f)
            if f in URL_FIELDS and not valid_url(val):  # must be a real URL, not prose
                continue
            proposed[f] = val

    return {
        "facility_type": ftype,
        "is_aesthetic_only": is_aesthetic_only,
        "confidence": confidence,
        "reason": obj.reason,
        "needs_review": needs_review,
        "proposed": proposed,
        "source": res["sources"][0] if res["sources"] else None,
        "sources": res["sources"],
        "queries": res["queries"],
        "research_text": res["text"],  # raw Gemini Phase-A response (for audit log)
    }


def fetch_facilities(supabase, limit: int | None, force: bool,
                     skip_labs: bool = False) -> list[dict]:
    cols = ("id,name,type,city,province,region,website,phone,address,services,"
            "booking_url,has_philhealth,enrichment_meta")
    rows = fetch_all(supabase, "facilities", cols)
    if not force:
        # Skip rows already processed (enrichment_meta.classification present).
        rows = [
            r for r in rows
            if not (isinstance(r.get("enrichment_meta"), dict)
                    and r["enrichment_meta"].get("classification"))
        ]
    if skip_labs:
        # Skip pathology/diagnostic labs — mostly not patient-facing for a
        # skin-cancer directory, and the bulk of the remaining enrichment cost.
        rows = [r for r in rows if r.get("type") != "pathology_lab"]
    if limit:
        rows = rows[:limit]
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Classify + fill blank facility fields (one pass).")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="Print only, no writes (default).")
    g.add_argument("--apply", action="store_true", help="Write to Supabase.")
    ap.add_argument("--limit", type=int, default=None, help="Process at most N facilities.")
    ap.add_argument("--force", action="store_true", help="Re-process already-done rows.")
    ap.add_argument("--skip-labs", action="store_true",
                    help="Skip pathology_lab facilities (cheaper, patient-facing focus).")
    args = ap.parse_args()
    apply = args.apply  # dry-run is the default when neither flag is set

    supabase = make_client()
    rows = fetch_facilities(supabase, args.limit, args.force, args.skip_labs)

    print("=" * 78)
    print(f"PHASE 2 — CLASSIFY + FILL FACILITIES  [{'APPLY' if apply else 'DRY-RUN'}]  model={MODEL}")
    print(f"Facilities to process: {len(rows)}")
    print("=" * 78)

    stats = {"processed": 0, "medical": 0, "aesthetic": 0, "mixed": 0,
             "unknown": 0, "aesthetic_only": 0, "needs_review": 0,
             "with_sources": 0, "rows_filled": 0, "fields_written": 0,
             "errored": 0, "written": 0}
    per_field = {f: 0 for f in ["services", *SCALAR_TARGETS]}

    for i, fac in enumerate(rows, 1):
        name = fac.get("name", "?")
        blanks = compute_blanks(fac)
        print(f"[{i}/{len(rows)}] {name[:46]:46} researching…", flush=True)
        try:
            r = process_one(fac, blanks)
        except GeminiQuotaError as e:
            print(f"\n{e}")
            print("Aborting run — no further calls made. Re-run when on a paid tier.")
            break
        except Exception as e:
            stats["errored"] += 1
            print(f"            ERROR {name[:40]}: {e}")
            traceback.print_exc(file=sys.stdout)
            rate_limit_sleep()
            continue

        stats["processed"] += 1
        stats[r["facility_type"]] += 1
        if r["is_aesthetic_only"]:
            stats["aesthetic_only"] += 1
        if r["needs_review"]:
            stats["needs_review"] += 1
        if r["sources"]:
            stats["with_sources"] += 1

        flag = " AESTHETIC-ONLY" if r["is_aesthetic_only"] else ""
        rev = " [needs_review]" if r["needs_review"] else ""
        print(f"            -> {r['facility_type']:9} conf={r['confidence']:.2f} "
              f"src={len(r['sources'])}{flag}{rev}")
        print(f"            reason: {r['reason'][:110]}")
        proposed = r["proposed"]
        if proposed:
            stats["rows_filled"] += 1
            for f, v in proposed.items():
                per_field[f] += 1
                print(f"            fill {f}: <blank> -> {v}   [{(r['source'] or 'no-src')[:50]}]")

        # Raw-response audit log (one JSONL line per facility, keyed by id).
        append_audit({
            "id": fac["id"],
            "name": name,
            "at": now_iso(),
            "model": MODEL,
            "applied": apply,
            "research_text": r["research_text"],
            "queries": r["queries"],
            "sources": r["sources"],
            "result": {
                "facility_type": r["facility_type"],
                "is_aesthetic_only": r["is_aesthetic_only"],
                "confidence": r["confidence"],
                "reason": r["reason"],
                "proposed_fills": r["proposed"],
            },
        })

        if apply:
            meta_updates = {
                "classification": {
                    "facility_type": r["facility_type"],
                    "is_aesthetic_only": r["is_aesthetic_only"],
                    "confidence": r["confidence"],
                    "reason": r["reason"],
                    "model": MODEL,
                    "at": now_iso(),
                },
                "sources": r["sources"],
                "queries": r["queries"],
            }
            if proposed:
                meta_updates["filled"] = {
                    f: {"value": v, "source_url": r["source"]} for f, v in proposed.items()
                }
            meta = merge_meta(fac.get("enrichment_meta"), meta_updates)
            update = {
                "facility_type": r["facility_type"],
                "is_aesthetic_only": r["is_aesthetic_only"],
                "classification_confidence": r["confidence"],
                "classification_reason": r["reason"],
                "needs_review": r["needs_review"],
                "enriched_by": MODEL,
                "enriched_at": now_iso(),
                "enrichment_meta": meta,
                # NOTE: status is deliberately NOT touched here.
                **proposed,  # blanks-only fills
            }
            try:
                supabase.table("facilities").update(update).eq("id", fac["id"]).execute()
                stats["written"] += 1
                stats["fields_written"] += len(proposed)
            except Exception as e:
                stats["errored"] += 1
                print(f"            DB ERROR: {e}")

        rate_limit_sleep()

    print("\n" + "=" * 78)
    print("SUMMARY")
    for k in ("processed", "medical", "aesthetic", "mixed", "unknown",
              "aesthetic_only", "needs_review", "with_sources",
              "rows_filled", "fields_written", "written", "errored"):
        print(f"  {k:16}: {stats[k]}")
    print("  fields proposed (per field):")
    for f, n in per_field.items():
        print(f"     {f:16}: {n}")
    if not apply:
        print("\n  DRY-RUN — nothing written. Re-run with --apply to persist.")
    write_report({"phase": "classify_fill", "apply": apply, "stats": stats,
                  "per_field": per_field}, "enrichment_report_classify.json")


if __name__ == "__main__":
    main()

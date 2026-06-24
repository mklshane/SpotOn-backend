"""Phase 5 — Doctors light-touch enrichment (conservative).

Fills a doctor's `website` from authoritative sources (PDS directory, hospital
sites, official clinic pages) when blank, and records clinic affiliation in
enrichment_meta. NO classification, NO personal-detail fishing, NO photos, NO
likeness — consistent with the project's privacy line. `doctors` has no `status`
column, so nothing is hidden here.

  python 08_enrich_doctors.py --dry-run --limit 15   # default: prints, no writes
  python 08_enrich_doctors.py --apply --limit 15
  python 08_enrich_doctors.py --apply
  python 08_enrich_doctors.py --apply --force        # re-enrich already-done rows
"""
from __future__ import annotations

import argparse
import sys
import traceback

from enrich_common import (
    append_audit,
    fetch_all,
    is_blank,
    make_client,
    merge_meta,
    now_iso,
    rate_limit_sleep,
    valid_url,
    write_report,
)
from enrich_schemas import DoctorEnrichment
from gemini_client import MODEL, GeminiQuotaError, extract, research


def research_prompt(doc: dict) -> str:
    bits = [f"Name: {doc.get('name','')}"]
    if doc.get("title"):
        bits.append(f"Title: {doc['title']}")
    for k in ("city", "region"):
        if doc.get(k):
            bits.append(f"{k.title()}: {doc[k]}")
    ident = "\n".join(bits)
    return (
        "You are finding the PUBLIC PROFESSIONAL website and clinic affiliation of "
        "a dermatologist in the Philippines, for a medical directory. Use web "
        "search and AUTHORITATIVE sources only: the Philippine Dermatological "
        "Society (PDS) directory, hospital websites, and official clinic pages.\n\n"
        f"{ident}\n\n"
        "Report ONLY: (1) the doctor's official professional website URL, if one "
        "exists, and (2) the name of the hospital or clinic they practice at. "
        "Do NOT report personal contact details, home address, photos, social "
        "media, or any private information. If you cannot confirm a fact from an "
        "authoritative source, say it is unknown — do not guess."
    )


def enrich_one(doc: dict) -> dict:
    res = research(research_prompt(doc))
    obj: DoctorEnrichment = extract(res["text"], DoctorEnrichment)
    source = res["sources"][0] if res["sources"] else None
    return {
        "website": obj.website if valid_url(obj.website) else None,
        "affiliation": None if is_blank(obj.clinic_affiliation) else obj.clinic_affiliation,
        "source": source,
        "sources": res["sources"],
        "queries": res["queries"],
        "research_text": res["text"],  # raw Gemini Phase-A response (for audit log)
    }


def fetch_doctors(supabase, limit: int | None, force: bool) -> list[dict]:
    cols = "id,name,title,city,region,website,enrichment_meta"
    rows = fetch_all(supabase, "doctors", cols)
    out = []
    for r in rows:
        meta = r.get("enrichment_meta")
        done = isinstance(meta, dict) and meta.get("light_touch")
        if done and not force:
            continue
        if not is_blank(r.get("website")):
            continue  # only target blank website
        out.append(r)
    if limit:
        out = out[:limit]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Light-touch doctor enrichment (website/affiliation).")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="Print only, no writes (default).")
    g.add_argument("--apply", action="store_true", help="Write to Supabase.")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    apply = args.apply

    supabase = make_client()
    rows = fetch_doctors(supabase, args.limit, args.force)

    print("=" * 78)
    print(f"PHASE 5 — DOCTORS LIGHT-TOUCH  [{'APPLY' if apply else 'DRY-RUN'}]  model={MODEL}")
    print(f"Doctors with blank website to process: {len(rows)}")
    print("=" * 78)

    stats = {"processed": 0, "website_written": 0, "rows_written": 0,
             "no_data": 0, "errored": 0}

    for i, doc in enumerate(rows, 1):
        name = doc.get("name", "?")
        print(f"[{i}/{len(rows)}] {name[:46]:46} researching…", flush=True)
        try:
            r = enrich_one(doc)
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
        append_audit({
            "id": doc["id"], "name": name, "at": now_iso(), "model": MODEL,
            "applied": apply,
            "research_text": r["research_text"],
            "queries": r["queries"], "sources": r["sources"],
            "result": {"website": r["website"], "affiliation": r["affiliation"]},
        }, name="enrichment_audit_doctors.jsonl")
        if not r["website"] and not r["affiliation"]:
            stats["no_data"] += 1
            print("            -> nothing found in authoritative sources (left blank).")
            rate_limit_sleep()
            continue
        if r["website"]:
            print(f"            website: <blank> -> {r['website']}   [{(r['source'] or '')[:55]}]")
        if r["affiliation"]:
            print(f"            affiliation (meta only): {r['affiliation']}")

        if apply:
            lt = {"affiliation": r["affiliation"],
                  "website_source": r["source"] if r["website"] else None,
                  "at": now_iso()}
            meta = merge_meta(doc.get("enrichment_meta"), {
                "light_touch": lt,
                "sources": r["sources"],
                "queries": r["queries"],
            })
            update = {"enriched_by": MODEL, "enriched_at": now_iso(),
                      "enrichment_meta": meta}
            if r["website"]:
                update["website"] = r["website"]
            try:
                supabase.table("doctors").update(update).eq("id", doc["id"]).execute()
                stats["rows_written"] += 1
                if r["website"]:
                    stats["website_written"] += 1
            except Exception as e:
                stats["errored"] += 1
                print(f"            DB ERROR: {e}")

        rate_limit_sleep()

    print("\n" + "=" * 78)
    print("SUMMARY")
    for k in ("processed", "website_written", "rows_written", "no_data", "errored"):
        print(f"  {k:16}: {stats[k]}")
    if not apply:
        print("\n  DRY-RUN — nothing written. Re-run with --apply to persist.")
    write_report({"phase": "doctors", "apply": apply, "stats": stats},
                 "enrichment_report_doctors.json")


if __name__ == "__main__":
    main()

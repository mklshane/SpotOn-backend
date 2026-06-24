"""Phase 4 — Apply exclusions (the ONLY step that hides clinics).

Human-gated and reversible. Selects high-confidence aesthetic-only facilities and
sets status='excluded'. The directory API (api/app/routers/directory.py) excludes
that status by default, so the rows disappear from results WITHOUT being deleted.

  python 07_exclude_facilities.py --dry-run              # default: list only, no writes
  python 07_exclude_facilities.py --dry-run --threshold 0.9
  python 07_exclude_facilities.py --apply                # flip status on the approved set

Only run --apply on the set a human approved at the Phase 2 review checkpoint.
"""
from __future__ import annotations

import argparse

from enrich_common import EXCLUDED_STATUS, make_client, now_iso, write_report

DEFAULT_THRESHOLD = 0.85


def fetch_candidates(supabase, threshold: float) -> list[dict]:
    cols = "id,name,city,classification_confidence,classification_reason,status,enrichment_meta"
    rows = (
        supabase.table("facilities")
        .select(cols)
        .eq("is_aesthetic_only", True)
        .gte("classification_confidence", threshold)
        .order("classification_confidence", desc=True)
        .execute()
        .data
        or []
    )
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Exclude high-confidence aesthetic-only clinics.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="List only, no writes (default).")
    g.add_argument("--apply", action="store_true", help="Set status='excluded' on the set.")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                    help=f"Min classification_confidence (default {DEFAULT_THRESHOLD}).")
    args = ap.parse_args()
    apply = args.apply

    supabase = make_client()
    rows = fetch_candidates(supabase, args.threshold)

    print("=" * 78)
    print(f"PHASE 4 — APPLY EXCLUSIONS  [{'APPLY' if apply else 'DRY-RUN'}]")
    print(f"Criteria: is_aesthetic_only = true AND confidence >= {args.threshold}")
    print(f"Candidates: {len(rows)}")
    print("=" * 78)

    written = 0
    skipped_already = 0
    for i, fac in enumerate(rows, 1):
        already = fac.get("status") == EXCLUDED_STATUS
        mark = " (already excluded)" if already else ""
        print(f"[{i}/{len(rows)}] {fac.get('name','?')[:42]:42} "
              f"conf={fac.get('classification_confidence')}  city={fac.get('city','')}{mark}")
        print(f"            reason: {(fac.get('classification_reason') or '')[:110]}")

        if apply and not already:
            meta = dict(fac.get("enrichment_meta") or {})
            meta["excluded"] = {
                "previous_status": fac.get("status"),
                "threshold": args.threshold,
                "at": now_iso(),
            }
            try:
                supabase.table("facilities").update(
                    {"status": EXCLUDED_STATUS, "enrichment_meta": meta}
                ).eq("id", fac["id"]).execute()
                written += 1
            except Exception as e:
                print(f"            DB ERROR: {e}")
        elif already:
            skipped_already += 1

    print("\n" + "=" * 78)
    print("SUMMARY")
    print(f"  candidates        : {len(rows)}")
    print(f"  newly excluded    : {written}")
    print(f"  already excluded  : {skipped_already}")
    if not apply:
        print("\n  DRY-RUN — nothing written. Review this list, then re-run with --apply.")
    else:
        print("\n  REVERSAL — to restore any excluded facility, run in Supabase SQL:")
        print("    update facilities set status = null where id = '<facility-uuid>';")
        print("  (or restore the previous status saved in enrichment_meta.excluded.previous_status)")

    write_report(
        {"phase": "exclude", "apply": apply, "threshold": args.threshold,
         "candidates": len(rows), "newly_excluded": written,
         "ids": [r["id"] for r in rows]},
        "enrichment_report_exclude.json",
    )


if __name__ == "__main__":
    main()

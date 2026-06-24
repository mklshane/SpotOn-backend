"""
03_scrape_bookaderma.py
=======================
Polite, robots-respecting collector of PUBLIC dermatologist listings from
BookaDerma, for the SpotOn telemedicine directory.

Compliance posture (why this "doesn't violate anything"):
  1. robots.txt is read and OBEYED (via requests, so no SSL-cert issues).
     If the listing path is disallowed, the script refuses. If robots.txt
     can't be confirmed, it aborts rather than guessing.
  2. Honest, identifiable User-Agent (purpose + contact). No spoofing.
  3. One GET of the public /browse-doctors page. No login, no private API.
  4. Honors any Crawl-delay; conservative default otherwise.
  5. Stores only PUBLIC professional info (name, specialties, location,
     consultation fee, rating, public booking URL). NEVER stores photos.
  6. Writes are idempotent (dedup by doctor + platform), so re-runs are safe.

BookaDerma is a Next.js App-Router site: the doctor data is embedded as
structured JSON in the React Server Component "flight" payload
(self.__next_f.push(...)), under `initialDoctors`. We read that directly —
it's cleaner and more stable than scraping rendered HTML.

Usage:
  python3 03_scrape_bookaderma.py --dry-run   # fetch + parse + print, NO DB writes
  python3 03_scrape_bookaderma.py             # live: upsert into Supabase
"""

import os
import re
import sys
import json
import time
import argparse
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from supabase import create_client, Client

# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

BASE = "https://bookaderma.com"
LISTING_URL = f"{BASE}/browse-doctors"
PLATFORM_SLUG = "bookaderma"

CONTACT = os.getenv("SCRAPER_CONTACT", "spoton-thesis@example.com")
USER_AGENT = f"SpotOnResearchBot/1.0 (+academic skin-cancer directory; contact: {CONTACT})"

DEFAULT_DELAY_SECONDS = 5.0
REQUEST_TIMEOUT = 25
RAW_DIR = Path("./data/raw")
RAW_DIR.mkdir(parents=True, exist_ok=True)

# Map BookaDerma's free-text specializations -> SpotOn's controlled vocab.
# Only skin-cancer-relevant tags are kept; everything else is ignored. A
# board-certified dermatologist with no mappable tag still gets the
# general_dermatology baseline (any derm can assess/refer a lesion).
SPECIALTY_MAP = {
    "general dermatology": "general_dermatology",
    "dermoscopy": "dermoscopy",
    "dermatologic surgery": "dermatologic_surgery",
    "dermatopathology": "dermatopathology",
}
PDS_MARKERS = (
    "dpds", "fpds", "philippine dermatological society",
    "diplomate, philippine derma", "fellow, philippine derma",
    "fellow of the philippine derma",
)


# ----------------------------------------------------------------------
# Compliance: robots.txt
# ----------------------------------------------------------------------
def robots_check(url: str) -> float:
    rp = RobotFileParser()
    try:
        resp = requests.get(f"{BASE}/robots.txt",
                            headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        sys.exit(f"ABORT: could not reach {BASE}/robots.txt ({e}). Not proceeding.")
    if resp.status_code == 404:
        rp.parse([])                      # no robots.txt => allowed by convention
    elif resp.status_code >= 400:
        sys.exit(f"ABORT: {BASE}/robots.txt returned HTTP {resp.status_code}; not proceeding.")
    else:
        rp.parse(resp.text.splitlines())
    if not rp.can_fetch(USER_AGENT, url):
        sys.exit(f"ABORT: robots.txt disallows {url} for this agent. Not fetching.")
    delay = rp.crawl_delay(USER_AGENT) or DEFAULT_DELAY_SECONDS
    print(f"  robots.txt: fetch allowed. Using {float(delay):.1f}s delay.")
    return float(delay)


def fetch(url: str, delay: float) -> str:
    time.sleep(delay)
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.text


# ----------------------------------------------------------------------
# Primary parser: RSC flight payload -> initialDoctors
# ----------------------------------------------------------------------
def _decode_flight(html: str) -> str:
    """Concatenate + decode every self.__next_f.push([1,"..."]) string chunk.
    Uses raw_decode so escaping (incl. stray ']' / ')' inside strings) is safe."""
    dec = json.JSONDecoder()
    marker = "self.__next_f.push([1,"
    out, i = [], 0
    while True:
        j = html.find(marker, i)
        if j == -1:
            break
        k = j + len(marker)
        while k < len(html) and html[k] in " \t\r\n":
            k += 1
        if k >= len(html) or html[k] != '"':
            i = k
            continue
        try:
            s, end = dec.raw_decode(html, k)
        except json.JSONDecodeError:
            i = k + 1
            continue
        out.append(s)
        i = end
    return "".join(out)


def _extract_initial_doctors(flight: str):
    """Bracket-match the initialDoctors array out of the decoded flight text."""
    p = flight.find('"initialDoctors":')
    if p == -1:
        return None
    start = flight.find("[", p)
    if start == -1:
        return None
    depth, in_str, esc = 0, False, False
    for idx in range(start, len(flight)):
        c = flight[idx]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(flight[start:idx + 1])
                except json.JSONDecodeError:
                    return None
    return None


def parse_flight(html: str):
    flight = _decode_flight(html)
    if not flight:
        return None
    docs = _extract_initial_doctors(flight)
    if docs:
        (RAW_DIR / "bookaderma_doctors.json").write_text(json.dumps(docs, indent=2))
        print(f"  saved {len(docs)} structured records -> data/raw/bookaderma_doctors.json")
    return docs


def parse_html_fallback(html: str):
    """Last-resort parser if the flight payload ever disappears."""
    soup = BeautifulSoup(html, "html.parser")
    seen, out = set(), []
    for a in soup.select('a[href*="/doctors/"]'):
        slug = a.get("href", "").rstrip("/").split("/doctors/")[-1]
        if not slug or slug in seen:
            continue
        seen.add(slug)
        text = a.get_text(" ", strip=True)
        fee_m = re.search(r"₱\s?([\d,]+)", text)
        name_m = re.search(r"(Dr\.?\s+.+?)(?:New|Available|Not available)", text)
        out.append({
            "_fallback": True,
            "slug": slug,
            "name": (name_m.group(1).strip() if name_m else slug.replace("-", " ").title()),
            "specialization": "",
            "totalFee": int(fee_m.group(1).replace(",", "")) if fee_m else None,
        })
    return out


# ----------------------------------------------------------------------
# Normalization
# ----------------------------------------------------------------------
def _clean(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def normalize(rec: dict) -> dict:
    first, last = _clean(rec.get("firstName")), _clean(rec.get("lastName"))
    name = _clean(rec.get("name") or f"{first} {last}")

    spec_raw = rec.get("specialization") or ""
    blob = f"{rec.get('qualifications','')} {rec.get('biography','')} {rec.get('description','')}".lower()

    mapped = {SPECIALTY_MAP[s.strip().lower()]
              for s in re.split(r"[,/]", spec_raw) if s.strip().lower() in SPECIALTY_MAP}
    if "dermatopath" in blob or "dermatopath" in spec_raw.lower():
        mapped.add("dermatopathology")
    if not mapped:
        mapped = {"general_dermatology"}

    pds = (any(m in blob for m in PDS_MARKERS)
           or "dpds" in name.lower() or "fpds" in name.lower())

    fee = rec.get("totalFee") or rec.get("consultationFee")
    try:
        fee = int(round(float(fee)))
    except (TypeError, ValueError):
        fee = None

    region = rec.get("region") or {}
    city = rec.get("city") or {}
    slot = rec.get("firstAvailableSlot")
    if not slot:
        avail = "Not available"
    else:
        try:
            avail = "Available " + datetime.fromisoformat(slot.replace("Z", "+00:00")).strftime("%b %d, %Y")
        except (ValueError, AttributeError):
            avail = "Available"

    rc = rec.get("reviewCount") or 0
    quals_first = _clean((rec.get("qualifications") or "").splitlines()[0]) if rec.get("qualifications") else ""

    return {
        "slug": rec.get("slug"),
        "name": name,
        "title": (quals_first[:80] or None),
        "specialties": sorted(mapped),
        "specialties_display": _clean(spec_raw) or None,
        "pds_certified": bool(pds),
        "city": _clean(city.get("name")) or None,
        "region": _clean(region.get("shortName") or region.get("name")) or None,
        "fee": fee,
        "rating": (rec.get("rating") if rc else None),
        "review_count": rc,
        "is_introductory_fee": bool(rec.get("excludePlatformFee")),
        "available_text": avail,
        "license_number": _clean(rec.get("licenseNumber")) or None,
        "url": urljoin(BASE + "/", f"doctors/{rec.get('slug')}") if rec.get("slug") else None,
    }


# ----------------------------------------------------------------------
# DB writes (idempotent)
# ----------------------------------------------------------------------
def get_platform_id(sb: Client) -> str:
    r = sb.table("telemedicine_platforms").select("id").eq("slug", PLATFORM_SLUG).execute()
    if not r.data:
        sys.exit(f"ABORT: platform '{PLATFORM_SLUG}' missing. Run migration 002.")
    return r.data[0]["id"]


def find_existing_doctor(sb: Client, name: str):
    clean = re.sub(r"(dr\.?|dra\.?|,?\s*md|,?\s*dpds|,?\s*fpds|,)", "", name.lower()).strip()
    words = [w for w in clean.split() if w]
    if len(words) < 2:
        return None
    r = sb.table("doctors").select("id,name").ilike("name", f"%{words[-1]}%").execute()
    for doc in (r.data or []):
        if words[0] in doc["name"].lower():
            return doc["id"]
    return None


def upsert_doctor(sb: Client, d: dict) -> str:
    existing = find_existing_doctor(sb, d["name"])
    note = "telemedicine listing" + (f"; PRC {d['license_number']}" if d.get("license_number") else "")
    fields = {
        "title": d["title"],
        "city": d["city"],
        "region": d["region"],
        "specialties_display": d["specialties_display"],
        "pds_certified": d["pds_certified"],
    }
    if existing:
        sb.table("doctors").update(fields).eq("id", existing).execute()
        return existing
    rec = {
        "name": d["name"], "specialties": d["specialties"],
        "source": "bookaderma", "collected_by": "bookaderma_scraper",
        "notes": note, **fields,
    }
    return sb.table("doctors").insert(rec).execute().data[0]["id"]


def upsert_booking_link(sb: Client, doctor_id: str, platform_id: str, d: dict) -> str:
    existing = sb.table("booking_links").select("id") \
        .eq("doctor_id", doctor_id).eq("platform_id", platform_id).execute()
    payload = {
        "url": d["url"], "consultation_fee": d["fee"],
        "rating": d["rating"], "review_count": d["review_count"],
        "is_introductory_fee": d["is_introductory_fee"],
        "available_text": d["available_text"],
        "is_active": True, "last_verified": date.today().isoformat(),
    }
    if existing.data:
        sb.table("booking_links").update(payload).eq("id", existing.data[0]["id"]).execute()
        return "updated"
    sb.table("booking_links").insert({"doctor_id": doctor_id, "platform_id": platform_id, **payload}).execute()
    return "inserted"


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def run(dry_run: bool):
    print("=" * 64)
    print(f"BookaDerma scraper  |  {'DRY RUN (no DB writes)' if dry_run else 'LIVE'}")
    print("=" * 64)

    delay = robots_check(LISTING_URL)
    print(f"  fetching {LISTING_URL} ...")
    html = fetch(LISTING_URL, delay)
    (RAW_DIR / "bookaderma_listing.html").write_text(html)

    records, shape = parse_flight(html), "flight(initialDoctors)"
    if not records:
        records, shape = parse_html_fallback(html), "html_fallback"
    print(f"  parsed {len(records)} record(s) via {shape}.\n")

    normalized = [normalize(r) for r in records]

    if dry_run:
        for d in normalized:
            print(f"  - {d['name']:<34} {','.join(d['specialties']):<42} "
                  f"₱{d['fee'] if d['fee'] is not None else '?':<5} "
                  f"{(str(d['rating'])+'★ ('+str(d['review_count'])+')') if d['rating'] else 'no reviews':<14} "
                  f"{d['city'] or ''}")
        print("\n  DRY RUN — nothing written. Inspect data/raw/bookaderma_doctors.json,")
        print("  then re-run without --dry-run to write to Supabase.")
        return

    if not all([SUPABASE_URL, SUPABASE_KEY]):
        sys.exit("ABORT: SUPABASE_URL / SUPABASE_SERVICE_KEY missing from .env")
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    platform_id = get_platform_id(sb)

    stats = {"doctors_new": 0, "doctors_matched": 0, "links_inserted": 0, "links_updated": 0}
    for d in normalized:
        if not d["name"] or not d["url"]:
            continue
        had = find_existing_doctor(sb, d["name"])
        doc_id = upsert_doctor(sb, d)
        stats["doctors_matched" if had else "doctors_new"] += 1
        stats["links_inserted" if upsert_booking_link(sb, doc_id, platform_id, d) == "inserted"
              else "links_updated"] += 1

    print("\n" + "=" * 64)
    for k, v in stats.items():
        print(f"  {k.replace('_',' ').title():<22} {v}")
    print("=" * 64)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="fetch + parse + print, no DB writes")
    run(ap.parse_args().dry_run)
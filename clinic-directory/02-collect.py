"""
Google Places API — Skin Cancer Directory Collector
====================================================
Searches for dermatology clinics, oncology centers, pathology labs,
and dermatologists across Philippine cities.

STRICT FILTERING: Excludes aesthetic/beauty/cosmetic clinics.
Only keeps facilities and doctors relevant to skin cancer
diagnosis, biopsy, treatment, and referral.

Free tier: 10,000 Text Search calls/month (Essentials fields).
Hard limit: Script stops at 5,000 calls (50% safety buffer).
Expected usage: ~330 calls = $0.

Run:
  python3 02_collect.py
"""

import os
import re
import json
import uuid
import time
import functools
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from supabase import create_client, Client, ClientOptions

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")

RUN_ID = str(uuid.uuid4())[:8]
RAW_DIR = Path("./data/raw")
RAW_DIR.mkdir(parents=True, exist_ok=True)

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.nationalPhoneNumber",
    "places.internationalPhoneNumber",
    "places.regularOpeningHours",
    "places.websiteUri",
    "places.rating",
    "places.googleMapsUri",
    "places.location",
    "places.types",
])

options = ClientOptions(postgrest_client_timeout=30.0)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY, options=options)
api_calls = 0
API_CALL_LIMIT = 5000

BLACKLIST = [
    "aesthetic", "aesthetics", "esthetics",
    "beauty", "beauty clinic", "beauty center",
    "spa", "day spa", "med spa", "medspa",
    "facial", "facials",
    "whitening", "skin whitening", "glutathione",
    "slimming", "weight loss",
    "hair removal", "laser hair",
    "tattoo", "tattoo removal",
    "nail salon", "nail spa",
    "waxing", "threading",
    "botox", "filler", "fillers",
    "liposuction", "lipo",
    "rhinoplasty", "nose job",
    "cosmetic surgery", "plastic surgery",
    "anti-aging", "anti aging",
    "rejuvenation", "rejuvenating",
    "glowing skin", "glass skin",
    "pimple removal", "acne facial",
    "skin care products", "skincare products",
    "beauty products",
    "wellness center", "wellness spa",
    "salon", "parlor",
]

WHITELIST = [
    "oncology", "onco",
    "cancer", "tumor", "tumour",
    "biopsy",
    "pathology", "pathologist", "histopathology",
    "melanoma",
    "carcinoma", "basal cell", "squamous cell",
    "dermatopathology",
    "dermoscopy",
    "surgical oncology",
    "mohs surgery",
    "excision",
    "skin cancer",
]

CITIES = [
    {"lat": 14.5995, "lon": 120.9842, "name": "Manila"},
    {"lat": 14.6760, "lon": 121.0437, "name": "Quezon City"},
    {"lat": 14.5547, "lon": 121.0244, "name": "Makati"},
    {"lat": 14.5876, "lon": 121.0614, "name": "Pasig"},
    {"lat": 14.5243, "lon": 120.9916, "name": "Paranaque"},
    {"lat": 14.5311, "lon": 121.0185, "name": "Taguig"},
    {"lat": 14.6507, "lon": 121.0498, "name": "Mandaluyong"},
    {"lat": 13.9411, "lon": 121.1632, "name": "Lipa Batangas"},
    {"lat": 13.7565, "lon": 121.0583, "name": "Batangas City"},
    {"lat": 14.3294, "lon": 120.9367, "name": "Dasmarinas"},
    {"lat": 14.2714, "lon": 121.4195, "name": "Lucena"},
    {"lat": 14.4791, "lon": 121.0150, "name": "Binan Laguna"},
    {"lat": 14.2042, "lon": 121.1645, "name": "San Pablo Laguna"},
    {"lat": 14.3500, "lon": 121.0400, "name": "Calamba"},
    {"lat": 14.5870, "lon": 121.0880, "name": "Antipolo"},
    {"lat": 15.4755, "lon": 120.5963, "name": "Angeles Pampanga"},
    {"lat": 14.7950, "lon": 120.9280, "name": "Malolos Bulacan"},
    {"lat": 15.4850, "lon": 120.9717, "name": "Cabanatuan"},
    {"lat": 16.4023, "lon": 120.5960, "name": "Baguio"},
    {"lat": 16.6159, "lon": 120.3210, "name": "San Fernando La Union"},
    {"lat": 10.3157, "lon": 123.8854, "name": "Cebu City"},
    {"lat": 10.6918, "lon": 122.5644, "name": "Iloilo City"},
    {"lat": 10.6840, "lon": 122.9740, "name": "Bacolod"},
    {"lat": 9.3068, "lon": 123.3054, "name": "Dumaguete"},
    {"lat": 11.2543, "lon": 124.9613, "name": "Tacloban"},
    {"lat": 7.1907, "lon": 125.4553, "name": "Davao City"},
    {"lat": 8.4542, "lon": 124.6319, "name": "Cagayan de Oro"},
    {"lat": 7.0736, "lon": 125.6130, "name": "General Santos"},
    {"lat": 6.9214, "lon": 122.0790, "name": "Zamboanga City"},
    {"lat": 8.9475, "lon": 125.5406, "name": "Butuan"},
]

KEYWORDS = [
    "skin cancer doctor",
    "skin cancer treatment",
    "oncodermatology",
    "dermoscopy clinic",
    "dermatopathology",
    "skin biopsy laboratory",
    "melanoma doctor",
    "dermatologist",
    "dermatology clinic",
    "dermatology hospital department",
    "pathology laboratory",
    "histopathology lab",
    "oncology hospital",
]


def retry(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        for attempt in range(3):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                err = str(e).lower()
                # Added "timeout" and "timed out" to the catch list
                if any(x in err for x in ["502", "bad gateway", "json could not", "timeout", "timed out", "connection"]):
                    wait = 10 * (attempt + 1)
                    print(f"      Supabase error (timeout/502) — retry {attempt+1}/3, waiting {wait}s...")
                    time.sleep(wait)
                else:
                    raise
        print("      Supabase still down after 3 retries — skipping")
        return None
    return wrapper

def is_relevant(place):
    name = place.get("displayName", {}).get("text", "").lower()
    types = place.get("types", [])
    for word in WHITELIST:
        if word in name:
            return True, f"whitelist: {word}"
    for word in BLACKLIST:
        if word in name:
            return False, f"blacklist: {word}"
    for bt in ["beauty_salon", "hair_care", "spa", "nail_salon"]:
        if bt in types:
            return False, f"google_type: {bt}"
    for ind in ["hospital", "medical center", "medical centre", "dermatologist",
                "dermatology", "derma clinic", "pathology", "laboratory",
                "diagnostic", "doctor", "dr.", "dra.", "md", "fpds", "dpds"]:
        if ind in name:
            return True, f"medical: {ind}"
    for mt in ["hospital", "doctor", "health"]:
        if mt in types:
            return True, f"google_type: {mt}"
    if "skin" in name or "clinic" in name:
        return True, "ambiguous: needs_review"
    return False, "no_medical_indicator"


def is_doctor(place):
    name = place.get("displayName", {}).get("text", "").lower()
    types = place.get("types", [])
    for p in [r'\bdr\.?\s', r'\bdra\.?\s', r',\s*md\b', r',\s*fpds\b',
              r',\s*dpds\b', r'\bmd,', r'\bmd\s*$']:
        if re.search(p, name):
            return True
    if "doctor" in types and "hospital" not in types:
        return True
    return False


def classify_facility(place, keyword):
    name = place.get("displayName", {}).get("text", "").lower()
    types = place.get("types", [])
    if "hospital" in types or "hospital" in name:
        if any(w in name for w in ["government", "public", "provincial", "city hospital", "medical center"]):
            return "government_hospital"
        return "private_hospital"
    if any(w in name for w in ["patholog", "histopath", "diagnostic", "laboratory"]):
        return "pathology_lab"
    if "oncolog" in name or "cancer" in name:
        return "oncology_center"
    if "derma" in name or "skin" in name:
        return "dermatology_clinic"
    if "medical center" in name:
        return "medical_center"
    if "patholog" in keyword or "histopath" in keyword:
        return "pathology_lab"
    if "oncolog" in keyword:
        return "oncology_center"
    return "dermatology_clinic"


def classify_doctor(place, keyword):
    name = place.get("displayName", {}).get("text", "").lower()
    if "onco" in name or "cancer" in name: return "oncodermatology"
    if "patholog" in name: return "dermatopathology"
    if "dermoscop" in name: return "dermoscopy"
    if "surg" in name: return "dermatologic_surgery"
    if "onco" in keyword or "cancer" in keyword: return "oncodermatology"
    if "patholog" in keyword: return "dermatopathology"
    if "dermoscop" in keyword: return "dermoscopy"
    if "surg" in keyword: return "dermatologic_surgery"
    return "general_dermatology"


def search(query, lat, lon):
    global api_calls
    if api_calls >= API_CALL_LIMIT:
        print(f"    HARD LIMIT ({API_CALL_LIMIT}) — stopping")
        return []
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_API_KEY,
        "X-Goog-FieldMask": FIELD_MASK,
    }
    body = {
        "textQuery": f"{query} Philippines",
        "locationBias": {"circle": {"center": {"latitude": lat, "longitude": lon}, "radius": 15000.0}},
        "maxResultCount": 20,
    }
    try:
        r = requests.post(SEARCH_URL, headers=headers, json=body, timeout=20)
        api_calls += 1
        if r.status_code == 429:
            print("    RATE LIMITED — waiting 60s...")
            time.sleep(60)
            r = requests.post(SEARCH_URL, headers=headers, json=body, timeout=20)
            api_calls += 1
        if r.status_code != 200:
            print(f"    API ERROR {r.status_code}: {r.text[:150]}")
            return []
        data = r.json()
        fname = f"{RUN_ID}_{query[:15].replace(' ','_')}_{lat:.1f}.json"
        (RAW_DIR / fname).write_text(json.dumps(data, indent=2))
        return data.get("places", [])
    except Exception as e:
        print(f"    REQUEST ERROR: {e}")
        return []


def extract_hours(place):
    periods = place.get("regularOpeningHours", {}).get("periods", [])
    wd, we = None, None
    for p in periods:
        o, c = p.get("open", {}), p.get("close", {})
        day = o.get("day", -1)
        ot = f"{o.get('hour',0):02d}:{o.get('minute',0):02d}"
        ct = f"{c.get('hour',0):02d}:{c.get('minute',0):02d}" if c else None
        if 1 <= day <= 5 and not wd and ct: wd = {"open": ot, "close": ct}
        if day in (0, 6) and not we and ct: we = {"open": ot, "close": ct}
    return (json.dumps(wd) if wd else None, json.dumps(we) if we else None)


def parse_location(place, search_city):
    addr = place.get("formattedAddress", "")
    parts = [p.strip() for p in addr.split(",")]
    if parts and parts[-1].lower().strip() == "philippines": parts = parts[:-1]
    if len(parts) >= 2: return parts[-2].strip(), parts[-1].strip()
    return search_city, "Unknown"


@retry
def find_facility(google_id, name):
    if google_id:
        r = supabase.table("facilities").select("id").eq("google_place_id", google_id).execute()
        if r.data: return r.data[0]
    if name and len(name) > 5:
        r = supabase.table("facilities").select("id").ilike("name", f"%{name[:25]}%").execute()
        if r.data: return r.data[0]
    return None


@retry
def find_doctor(name):
    if not name or len(name) < 5: return None
    clean = re.sub(r'(dr\.?|dra\.?|md|fpds|dpds|,)', '', name.lower()).strip()
    words = clean.split()
    if len(words) < 2: return None
    r = supabase.table("doctors").select("id,name").ilike("name", f"%{words[-1]}%").execute()
    if r.data:
        for doc in r.data:
            if words[0] in doc["name"].lower(): return doc
    return None


@retry
def save_facility(place, keyword, search_city):
    name = place.get("displayName", {}).get("text", "")
    loc = place.get("location", {})
    lat, lon = loc.get("latitude"), loc.get("longitude")
    if not name or not lat or not lon: return None
    existing = find_facility(place.get("id"), name)
    if existing: return existing["id"]
    city, province = parse_location(place, search_city)
    wd, we = extract_hours(place)
    record = {
        "name": name,
        "type": classify_facility(place, keyword),
        "address": place.get("formattedAddress", ""),
        "city": city, "province": province,
        "latitude": lat, "longitude": lon,
        "phone": place.get("nationalPhoneNumber") or place.get("internationalPhoneNumber"),
        "website": place.get("websiteUri"),
        "google_maps_url": place.get("googleMapsUri"),
        "google_place_id": place.get("id"),
        "google_rating": place.get("rating"),
        "weekday_hours": wd, "weekend_hours": we,
        "collected_by": "google_places",
        "notes": f"keyword: {keyword}",
    }
    try:
        r = supabase.table("facilities").insert(record).execute()
        return r.data[0]["id"] if r.data else None
    except Exception as e:
        if "duplicate" not in str(e).lower() and "unique" not in str(e).lower():
            print(f"      DB ERROR (facility): {e}")
        return None


@retry
def save_doctor(place, keyword):
    name = place.get("displayName", {}).get("text", "")
    if not name: return None
    existing = find_doctor(name)
    if existing: return existing["id"]
    name_upper = name.upper()
    title_match = re.search(r'(?:,\s*)((?:MD|FPDS|DPDS|FMDS|MPH)[\w\s,]*)', name)
    record = {
        "name": name,
        "title": title_match.group(1).strip() if title_match else None,
        "specialization": classify_doctor(place, keyword),
        "pds_certified": "FPDS" in name_upper or "DPDS" in name_upper,
        "phone": place.get("nationalPhoneNumber") or place.get("internationalPhoneNumber"),
        "website": place.get("websiteUri"),
        "google_maps_url": place.get("googleMapsUri"),
        "google_place_id": place.get("id"),
        "source": "google_places",
        "collected_by": "google_places",
        "notes": f"keyword: {keyword}",
    }
    try:
        r = supabase.table("doctors").insert(record).execute()
        return r.data[0]["id"] if r.data else None
    except Exception as e:
        if "duplicate" not in str(e).lower() and "unique" not in str(e).lower():
            print(f"      DB ERROR (doctor): {e}")
        return None


@retry
def save_link(doctor_id, facility_id):
    if not doctor_id or not facility_id: return
    r = supabase.table("doctor_facility").select("id") \
        .eq("doctor_id", doctor_id).eq("facility_id", facility_id).execute()
    if r.data: return
    try:
        supabase.table("doctor_facility").insert({
            "doctor_id": doctor_id, "facility_id": facility_id, "is_primary": True,
        }).execute()
    except Exception: pass


def run():
    global api_calls
    print("=" * 60)
    print("SKIN CANCER CLINIC DIRECTORY — GOOGLE PLACES COLLECTOR")
    print(f"Run: {RUN_ID}")
    print(f"Cities: {len(CITIES)} | Keywords: {len(KEYWORDS)}")
    print(f"Estimated API calls: ~{len(CITIES) * len(KEYWORDS)}")
    print(f"Hard limit: {API_CALL_LIMIT} (free tier: 10,000/month)")
    print("=" * 60)

    stats = {"facilities": 0, "doctors": 0, "rejected_aesthetic": 0,
             "rejected_irrelevant": 0, "duplicates": 0, "total_results": 0}
    rejected_log = []

    for ci, city in enumerate(CITIES[15:], start=15):
        if api_calls >= API_CALL_LIMIT:
            print(f"\n  HARD LIMIT — stopping at {api_calls} calls")
            break
        print(f"\n[{ci+1}/{len(CITIES)}] {city['name']}")
        city_f, city_d = 0, 0

        for keyword in KEYWORDS:
            if api_calls >= API_CALL_LIMIT: break
            time.sleep(1.0)
            places = search(f"{keyword} {city['name']}", city["lat"], city["lon"])

            for place in places:
                stats["total_results"] += 1
                name = place.get("displayName", {}).get("text", "")
                relevant, reason = is_relevant(place)
                if not relevant:
                    if "blacklist" in reason: stats["rejected_aesthetic"] += 1
                    else: stats["rejected_irrelevant"] += 1
                    rejected_log.append({"name": name, "reason": reason, "city": city["name"]})
                    continue

                if is_doctor(place):
                    doc_id = save_doctor(place, keyword)
                    if doc_id:
                        city_d += 1
                        loc = place.get("location", {})
                        if loc.get("latitude"):
                            fac_id = save_facility(place, keyword, city["name"])
                            if fac_id: save_link(doc_id, fac_id)
                    else: stats["duplicates"] += 1
                else:
                    fac_id = save_facility(place, keyword, city["name"])
                    if fac_id: city_f += 1
                    else: stats["duplicates"] += 1

        stats["facilities"] += city_f
        stats["doctors"] += city_d
        print(f"  +{city_f} facilities, +{city_d} doctors | API: {api_calls}/{API_CALL_LIMIT}")

    rejected_path = RAW_DIR / f"rejected_{RUN_ID}.json"
    with open(rejected_path, "w") as f:
        json.dump(rejected_log, f, indent=2)

    print("\n" + "=" * 60)
    print("COMPLETE")
    print("=" * 60)
    print(f"  Results processed:     {stats['total_results']}")
    print(f"  Facilities saved:      {stats['facilities']}")
    print(f"  Doctors saved:         {stats['doctors']}")
    print(f"  Rejected (aesthetic):  {stats['rejected_aesthetic']}")
    print(f"  Rejected (irrelevant): {stats['rejected_irrelevant']}")
    print(f"  Duplicates skipped:    {stats['duplicates']}")
    print(f"  API calls used:        {api_calls}/{API_CALL_LIMIT}")
    print(f"  Rejected log:          {rejected_path}")
    print(f"\n  Next steps:")
    print(f"  1. Review rejected log for valid clinics")
    print(f"  2. Review data in Supabase Table Editor")
    print(f"  3. Run PDS directory script")
    print(f"  4. Manual verification for CALABARZON and NCR")


if __name__ == "__main__":
    if not GOOGLE_API_KEY:
        print("ERROR: Add GOOGLE_PLACES_API_KEY to .env")
        exit(1)
    if not SUPABASE_URL:
        print("ERROR: Add SUPABASE_URL to .env")
        exit(1)
    run()
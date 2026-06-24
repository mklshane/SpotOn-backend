"""Two-phase Gemini primitives for grounded enrichment.

Gemini 2.5 cannot combine the google_search tool with a JSON response_schema in
one call (returns 400 "controlled generation is not supported with google_search
tool"). So enrichment is two calls per record:

  Phase A  research(prompt)        -> grounded prose + source URIs + search queries
  Phase B  extract(notes, schema)  -> strict JSON (no tools), parsed into pydantic

Run a quick smoke test with:
  python gemini_client.py
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from google import genai
from google.genai import types
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

load_dotenv()

_API_KEY = os.getenv("GEMINI_API_KEY")
if not _API_KEY:
    raise SystemExit("ERROR: GEMINI_API_KEY missing from .env")

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Per-request timeout (ms). Without this, a hung socket freezes the whole run
# indefinitely — a timed-out call instead raises and is retried by tenacity, and a
# persistently-failing row is skipped. Grounded search can be slow, so keep it generous.
client = genai.Client(
    api_key=_API_KEY,
    http_options=types.HttpOptions(timeout=120_000),  # 120s
)


class GeminiQuotaError(RuntimeError):
    """A 429 that will NOT recover within this run — abort, don't retry.

    Covers prepaid-credit depletion and free-tier per-day quota exhaustion
    (e.g. 20 requests/day). Per-minute rate limits are NOT this — those retry.
    Enable billing / use a paid key, then re-run; phases are resumable.
    """


_QUOTA_MSG = (
    "Gemini quota wall hit (429 RESOURCE_EXHAUSTED). This key is either on the "
    "FREE TIER (~20 requests/day for gemini-2.5-flash) or out of prepaid credits "
    "— far too little for a full run. Enable billing / use a paid key at "
    "https://ai.studio/projects, then re-run — phases are resumable so finished "
    "rows are not redone."
)


def _is_fatal_quota(exc: BaseException) -> bool:
    """True for non-recoverable 429s: credit depletion or per-DAY/free-tier caps.

    Per-minute (RPM) limits contain 'PerMinute' and are left retryable.
    """
    msg = str(exc).lower()
    return (
        "credits are depleted" in msg
        or "prepayment credits" in msg
        or "free_tier" in msg
        or "perday" in msg.replace(" ", "").replace("_", "")
        or "requests per day" in msg
    )

# Phase A: grounded search, low temperature for factual answers.
GROUNDED = types.GenerateContentConfig(
    tools=[types.Tool(google_search=types.GoogleSearch())],
    temperature=0.1,
)

EXTRACT_INSTRUCTIONS = (
    "You extract structured data from RESEARCH NOTES about a medical/cosmetic "
    "clinic in the Philippines. Use ONLY the information in the notes below. "
    "If the notes do not clearly support a field, leave it null (or an empty "
    "list for services). NEVER guess, invent, or infer a value that is not "
    "stated in the notes — null is the correct answer when the notes are silent. "
    "The `services` list may contain ONLY values from the provided enum; drop "
    "anything else. Phone/website/address must be copied verbatim from the notes.\n\n"
    "RESEARCH NOTES:\n"
)


def _is_transient(exc: BaseException) -> bool:
    """Retry on rate limits (429) and transient server errors (5xx).

    Credit depletion / per-day caps also return 429 but won't recover within the
    run — never retry those.
    """
    if _is_fatal_quota(exc):
        return False
    msg = str(exc).lower()
    transient = ["429", "resource_exhausted", "rate limit", "rate-limit",
                 "500", "internal", "503", "unavailable", "502", "bad gateway",
                 "deadline", "timeout", "timed out", "connection"]
    return any(t in msg for t in transient)


_retry = retry(
    retry=retry_if_exception(_is_transient),
    wait=wait_exponential(multiplier=4, min=4, max=120),
    stop=stop_after_attempt(5),
    reraise=True,
)


def _guard_quota(exc: BaseException) -> None:
    """Translate a fatal-quota 429 into a clean, non-retryable abort."""
    if _is_fatal_quota(exc):
        raise GeminiQuotaError(_QUOTA_MSG) from None


@_retry
def _research(prompt: str) -> dict:
    """Grounded research call. Returns {text, sources, queries}.

    `sources` are the grounding_chunks web URIs (often Google redirect URLs
    under vertexaisearch.cloud.google.com — stored as-is for provenance).
    """
    r = client.models.generate_content(model=MODEL, contents=prompt, config=GROUNDED)

    sources: list[str] = []
    queries: list[str] = []
    cand = (r.candidates or [None])[0]
    md = getattr(cand, "grounding_metadata", None) if cand else None
    if md:
        for chunk in (md.grounding_chunks or []):
            web = getattr(chunk, "web", None)
            if web and getattr(web, "uri", None):
                sources.append(web.uri)
        queries = list(md.web_search_queries or [])

    return {
        "text": r.text or "",
        "sources": list(dict.fromkeys(sources)),  # de-dupe, keep order
        "queries": queries,
    }


@_retry
def _extract(notes: str, schema: type):
    """Second call: no tools, strict response_schema. Returns parsed pydantic."""
    cfg = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema,
        temperature=0,
    )
    r = client.models.generate_content(
        model=MODEL, contents=EXTRACT_INSTRUCTIONS + notes, config=cfg
    )
    return r.parsed


def research(prompt: str) -> dict:
    try:
        return _research(prompt)
    except Exception as e:  # noqa: BLE001 — translate fatal-quota 429 then re-raise
        _guard_quota(e)
        raise


def extract(notes: str, schema: type):
    try:
        return _extract(notes, schema)
    except Exception as e:  # noqa: BLE001
        _guard_quota(e)
        raise


def _smoke_test() -> None:
    from enrich_schemas import FacilityEnrichment

    print(f"Model: {MODEL}")
    print("\n[Phase A] research(...) — grounded:")
    res = research(
        "Research the dermatology clinic 'Skin and Cancer Foundation' in Makati, "
        "Philippines. What services does it offer? Does it provide medical "
        "dermatology, skin lesion evaluation, biopsy, or skin-cancer care, or is "
        "it a purely cosmetic/aesthetic clinic? Include phone, website, address."
    )
    print(f"  text chars : {len(res['text'])}")
    print(f"  sources    : {len(res['sources'])}")
    for s in res["sources"][:3]:
        print(f"     - {s}")
    print(f"  queries    : {res['queries']}")
    if not res["text"] or not res["sources"]:
        print("  WARNING: empty text or no sources — grounding may not be working.")

    print("\n[Phase B] extract(...) — strict schema:")
    obj = extract(res["text"], FacilityEnrichment)
    print(f"  parsed: {obj!r}")
    print("\nSmoke test OK.")


if __name__ == "__main__":
    # Smoke test — confirms the key + model support grounding and extraction.
    import sys

    try:
        _smoke_test()
    except GeminiQuotaError as e:
        print(f"\n{e}")
        sys.exit(2)

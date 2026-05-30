"""Fetch structured facts from Wikidata to ground LLM generation.

This complements Wikipedia grounding:
  - Wikipedia: good narrative extract (dates/events in prose)
  - Wikidata: structured fields (instance of, inception, coordinates, etc.)

We keep this intentionally small + robust: a few high-signal fields with URLs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from shorts_pipeline.logging_setup import get_logger

log = get_logger(__name__)

_TIMEOUT = 15.0

_HEADERS = {
    "User-Agent": "shorts-pipeline/1.0 (educational-automation; contact: https://runpod.ai/)",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

# APIs
_WIKIDATA_API = "https://www.wikidata.org/w/api.php"
_SPARQL = "https://query.wikidata.org/sparql"


@dataclass
class WikidataGrounding:
    query: str
    qid: str
    label: str
    description: str
    source_url: str
    facts: dict[str, str]
    found: bool = True


def _http_get_json(url: str, *, params: dict[str, str] | None = None, timeout: float) -> Any:
    with httpx.Client(headers=_HEADERS, timeout=timeout, follow_redirects=True) as client:
        r = client.get(url, params=params)
    if r.status_code >= 400:
        raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
    return r.json()


def _sparql(query: str, *, timeout: float) -> Any:
    with httpx.Client(headers={**_HEADERS, "Accept": "application/sparql-results+json"}, timeout=timeout) as client:
        r = client.get(_SPARQL, params={"format": "json", "query": query})
    if r.status_code >= 400:
        # Include a short body preview for debugging (Wikidata returns helpful messages).
        body = ""
        try:
            body = (r.text or "")[:400]
        except Exception:
            body = ""
        raise httpx.HTTPStatusError(f"HTTP {r.status_code}: {body}", request=r.request, response=r)
    return r.json()


def _search_qid(text: str, *, timeout: float) -> tuple[str, str, str] | None:
    """Return (qid, label, description) for the top hit."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        data = _http_get_json(
            _WIKIDATA_API,
            params={
                "action": "wbsearchentities",
                "search": text,
                "language": "en",
                "format": "json",
                "limit": "1",
            },
            timeout=timeout,
        )
        results = data.get("search") or []
        if not results:
            return None
        top = results[0]
        qid = str(top.get("id") or "").strip()
        label = str(top.get("label") or "").strip()
        desc = str(top.get("description") or "").strip()
        if qid.startswith("Q") and qid[1:].isdigit():
            return qid, label, desc
    except Exception as e:
        log.warning("wikidata_search_error", query=text, error=str(e)[:300])
    return None


def fetch_wikidata_grounding(query: str, *, timeout: float = _TIMEOUT) -> WikidataGrounding:
    hit = _search_qid(query, timeout=timeout)
    if not hit:
        return WikidataGrounding(
            query=query,
            qid="",
            label="",
            description="",
            source_url="",
            facts={},
            found=False,
        )
    qid, label, desc = hit
    url = f"https://www.wikidata.org/wiki/{quote(qid)}"

    # A compact set of fields that apply to people/events/objects reasonably well.
    # We use OPTIONALs so this works across niches/topics.
    # Keep this minimal; avoid label SERVICE complexity that sometimes 400s on the endpoint.
    sparql = f"""
SELECT ?instanceOf ?inception ?dob ?dod ?country ?locatedIn WHERE {{
  BIND(wd:{qid} AS ?item)
  OPTIONAL {{ ?item wdt:P31  ?instanceOf . }}
  OPTIONAL {{ ?item wdt:P571 ?inception . }}
  OPTIONAL {{ ?item wdt:P569 ?dob . }}
  OPTIONAL {{ ?item wdt:P570 ?dod . }}
  OPTIONAL {{ ?item wdt:P17  ?country . }}
  OPTIONAL {{ ?item wdt:P131 ?locatedIn . }}
}}
LIMIT 1
""".strip()

    facts: dict[str, str] = {}
    try:
        data = _sparql(sparql, timeout=timeout)
        bindings = (data.get("results") or {}).get("bindings") or []
        if bindings:
            b0 = bindings[0]
            def v(key: str) -> str:
                node = b0.get(key) or {}
                return str(node.get("value") or "").strip()

            if v("instanceOf"):
                facts["instance_of"] = v("instanceOf")
            if v("inception"):
                facts["inception"] = v("inception")
            if v("dob"):
                facts["date_of_birth"] = v("dob")
            if v("dod"):
                facts["date_of_death"] = v("dod")
            if v("country"):
                facts["country"] = v("country")
            if v("locatedIn"):
                facts["located_in"] = v("locatedIn")
    except Exception as e:
        log.warning("wikidata_sparql_error", qid=qid, error=str(e)[:300])

    log.info("wikidata_grounding_fetched", query=query, qid=qid, facts=len(facts))
    return WikidataGrounding(
        query=query,
        qid=qid,
        label=label,
        description=desc,
        source_url=url,
        facts=facts,
        found=True,
    )


def format_wikidata_for_prompt(g: WikidataGrounding) -> str:
    if not g.found or not g.qid:
        return ""
    lines = []
    if g.label:
        lines.append(f"ENTITY: {g.label}")
    if g.description:
        lines.append(f"DESCRIPTION: {g.description}")
    lines.append(f"SOURCE: {g.source_url}")
    if g.facts:
        for k, val in g.facts.items():
            lines.append(f"{k.upper()}: {val}")
    else:
        lines.append("FACTS: (none returned)")
    return (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"VERIFIED FACTS — WIKIDATA: {g.qid}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "The following is sourced from Wikidata (structured facts).\n"
        "Use it to verify entity type and dates. If a detail is missing, do NOT invent it.\n\n"
        + "\n".join(lines)
        + "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "END OF WIKIDATA FACTS.\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


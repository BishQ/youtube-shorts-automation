"""Fetch verified Wikipedia facts to ground LLM generation.

This is a lightweight "RAG" step: pull a small, citeable fact pack from
Wikipedia / MediaWiki before writing the script. The planner is instructed
to ONLY use these facts for dates, names, locations, and outcomes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from shorts_pipeline.logging_setup import get_logger

log = get_logger(__name__)

_WIKI_REST    = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
_WIKI_API     = (
    "https://en.wikipedia.org/w/api.php"
    "?action=query&prop=extracts&exintro=false&explaintext=true"
    "&exsectionformat=plain&titles={title}&format=json&redirects=1&origin=*"
)
_WIKI_SEARCH  = (
    "https://en.wikipedia.org/w/api.php"
    "?action=opensearch&search={query}&limit=1&namespace=0&format=json&origin=*"
)
_TIMEOUT = 15.0
_MAX_EXTRACT_CHARS = 6000
_HEADERS = {
    # Wikipedia REST/API endpoints frequently 403 generic user agents.
    # Provide contact info per Wikimedia API etiquette.
    "User-Agent": "shorts-pipeline/1.0 (educational-automation; contact: https://runpod.ai/)",
    "Accept": "application/json,text/plain;q=0.9,*/*;q=0.1",
    "Accept-Language": "en-US,en;q=0.9",
}


def _http_get(url: str, *, timeout: float) -> httpx.Response:
    # follow_redirects helps with canonicalization and some edge titles
    with httpx.Client(headers=_HEADERS, timeout=timeout, follow_redirects=True) as client:
        return client.get(url)


@dataclass
class WikiGrounding:
    title: str
    summary: str
    extract: str
    source_url: str
    found: bool = True


def _clean(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def _normalize_query_name(name: str) -> str:
    """Remove numbered-list prefixes before querying Wikipedia."""
    return re.sub(r"^\s*\d+[\.)]\s*", "", name).strip()


def _to_wiki_title(name: str) -> str:
    """Normalize a figure name to a Wikipedia-style title (Title Case, underscores)."""
    return _normalize_query_name(name).title().replace(" ", "_")


def _resolve_canonical_title(query: str, *, timeout: float) -> str | None:
    """Use OpenSearch to find the canonical Wikipedia title for a search query."""
    try:
        r = _http_get(_WIKI_SEARCH.format(query=quote(query)), timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            titles = data[1] if len(data) > 1 else []
            if titles:
                return titles[0].replace(" ", "_")
    except Exception as e:
        log.warning("wiki_opensearch_error", error=str(e))
    return None


def fetch_grounding(figure_name: str, *, timeout: float = _TIMEOUT) -> WikiGrounding:
    """
    Fetch Wikipedia summary + intro extract for figure_name.
    Returns WikiGrounding with found=False if the page cannot be retrieved.
    Never raises — on any error returns found=False so the pipeline continues.
    """
    query_name = _normalize_query_name(figure_name)
    title = _to_wiki_title(query_name)

    # Step 1: short summary via REST API
    summary = ""
    try:
        rest_url = _WIKI_REST.format(title=quote(title, safe=""))
        r = _http_get(rest_url, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            summary = _clean(data.get("extract", ""))
            title = data.get("title", title).replace(" ", "_")
        elif r.status_code == 404:
            log.warning("wiki_not_found", figure=figure_name, query=query_name)
            return WikiGrounding(title=query_name, summary="", extract="", source_url="", found=False)
        elif r.status_code == 403:
            # Some articles are restricted on the REST endpoint — try OpenSearch resolution
            log.warning("wiki_rest_403", figure=figure_name, query=query_name, title=title)
            canonical = _resolve_canonical_title(query_name, timeout=timeout)
            if canonical and canonical.lower() != title.lower():
                log.info("wiki_canonical_resolved", original=title, canonical=canonical)
                title = canonical
                # Retry with resolved title
                rest_url = _WIKI_REST.format(title=quote(title, safe=""))
                r2 = _http_get(rest_url, timeout=timeout)
                if r2.status_code == 200:
                    data = r2.json()
                    summary = _clean(data.get("extract", ""))
                    title = data.get("title", title).replace(" ", "_")
        else:
            log.warning("wiki_rest_unexpected_status", status=r.status_code, figure=figure_name, query=query_name)
    except Exception as e:
        log.warning("wiki_summary_error", error=str(e))

    # Step 2: longer intro extract via MediaWiki API
    extract = summary
    try:
        api_url = _WIKI_API.format(title=quote(title, safe=""))
        r3 = _http_get(api_url, timeout=timeout)
        if r3.status_code == 200:
            pages = r3.json().get("query", {}).get("pages", {})
            for page in pages.values():
                raw = page.get("extract", "")
                if raw:
                    extract = _clean(raw)[:_MAX_EXTRACT_CHARS]
                    break
        elif r3.status_code == 403:
            log.warning("wiki_api_403", figure=figure_name, query=query_name, title=title)
    except Exception as e:
        log.warning("wiki_extract_error", error=str(e))

    if not summary and not extract:
        return WikiGrounding(title=query_name, summary="", extract="", source_url="", found=False)

    log.info("wiki_grounding_fetched", figure=figure_name,
             summary_chars=len(summary), extract_chars=len(extract))
    page_url = f"https://en.wikipedia.org/wiki/{quote(title)}"
    return WikiGrounding(title=title, summary=summary, extract=extract, source_url=page_url, found=True)


def format_for_prompt(g: WikiGrounding) -> str:
    """Return a block to inject into the system prompt as verified facts."""
    if not g.found or not g.extract:
        return ""
    return (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"VERIFIED FACTS — WIKIPEDIA: {g.title.replace('_', ' ').upper()}\n"
        f"SOURCE: {g.source_url}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "The following is sourced from Wikipedia. You MUST base all dates, events,\n"
        "locations, and outcomes ONLY on the facts below.\n"
        "If a fact you want to include is NOT in this text, do NOT include it.\n"
        "Do not invent details. Do not extrapolate. Do not add dramatic embellishment\n"
        "that contradicts or extends beyond what is written here.\n\n"
        + g.extract
        + "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "END OF VERIFIED FACTS. Write your JSON using ONLY the above.\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

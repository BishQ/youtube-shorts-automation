"""Multi-source grounding for the planner (Wikipedia + Wikidata).

Goal: reduce hallucinations by injecting verified facts into the system prompt.
"""

from __future__ import annotations

from dataclasses import dataclass

from shorts_pipeline.planner.wiki_grounding import WikiGrounding, fetch_grounding, format_for_prompt
from shorts_pipeline.planner.wikidata_grounding import (
    WikidataGrounding,
    fetch_wikidata_grounding,
    format_wikidata_for_prompt,
)


@dataclass
class MultiGrounding:
    wikipedia: WikiGrounding
    wikidata: WikidataGrounding

    @property
    def found_any(self) -> bool:
        return bool(self.wikipedia.found or self.wikidata.found)


def fetch_multi_grounding(topic: str) -> MultiGrounding:
    # Wikipedia first (prose facts), Wikidata second (structured).
    wiki = fetch_grounding(topic)
    wd = fetch_wikidata_grounding(topic)
    return MultiGrounding(wikipedia=wiki, wikidata=wd)


def format_multi_for_prompt(g: MultiGrounding) -> str:
    blocks = []
    w = format_for_prompt(g.wikipedia)
    if w:
        blocks.append(w)
    wd = format_wikidata_for_prompt(g.wikidata)
    if wd:
        blocks.append(wd)
    return "\n\n".join(blocks).strip()


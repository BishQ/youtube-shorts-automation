"""Niche: Facts (alias of edutainment).

Scope: surprising-but-true facts catalogue from topic creator's
``niche_progress/facts/`` source. Same craft rules as edutainment —
Vsauce / Tom Scott / TED-demo register. Curiosity-first, never clickbait.

This module is a thin wrapper: it re-exports the edutainment
``SYSTEM_PROMPT`` and ``user_prompt`` so the ``facts`` niche-progress
batches can be fed into the pipeline via ``make_scripts.py facts ...``
without duplicating 215 lines of prompt content.

If the facts catalogue's voice ever diverges from edutainment (e.g.
faster, more list-shaped, less mechanism-led), copy edutainment.py's
contents here and edit — the import below is a convenience, not a
contract.
"""
from .edutainment import SYSTEM_PROMPT, user_prompt

__all__ = ["SYSTEM_PROMPT", "user_prompt"]

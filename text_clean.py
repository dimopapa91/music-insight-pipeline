"""Shared text-cleanup helpers for AI-generated copy.

strip_em_dashes() is the enforcement layer for the "no em dashes" instruction
already present in every Claude prompt in this codebase (pipeline.py's
analyse_with_claude(), views_artist.py's _cached_compare_verdict(), and
views_taste.py's taste_profile()). A prompt instruction is a request, not a
guarantee: Claude sometimes emits an em dash anyway. This makes the no-dash
promise actually hold by rewriting generated text deterministically right
after generation, before it is cached or saved, so a slip on the model's
part never reaches the database, the in-memory cache, or the page.

scripts/strip_emdashes.py (the one-off backfill for content generated
before this existed) imports this exact function too, so historical and
newly-generated text are cleaned identically.
"""
import re

_DASH_RE = re.compile(r"\s*[—–]\s*")  # em (—) or en (–) dash


def strip_em_dashes(text):
    """Replace em/en dashes with a comma, tidy spacing. Hyphens are left alone.

    Safe on falsy input (None, "") -- returns it unchanged, since callers
    already handle "" as the schema-compatible "unavailable" representation.
    """
    if not text:
        return text
    out = _DASH_RE.sub(", ", text)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    return out

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


# ── data-source names in AI copy (27 Sep 2026) ──────────────────────────
#
# The site no longer names where its data comes from (attribution lives on
# the About page), but older AI insights say things like "his most popular
# tracks on Last.fm reveal…". The prompts now ask Claude not to name
# sources; strip_source_mentions() enforces it deterministically, both on
# new text and whenever stored text is shown or served. User-written posts
# are never passed through it.

_SRC = r"(?:Last\.?\s?fm|Spotify|Deezer|MusicBrainz|ListenBrainz)"
_SRCS = rf"{_SRC}(?:(?:,\s*|\s+and\s+|\s*/\s*){_SRC})*"
_KIND = (r"(?:data|numbers|stats|statistics|figures|charts|metrics|listeners|listener counts|listener|"
         r"users|scrobbles|scrobble counts|plays|play counts|playcounts|streams|streaming numbers|"
         r"popularity|popularity scores?|audience|followers|fans|playlists|tags|top tracks)")

_SOURCE_PATTERNS = [
    # "(Last.fm)", "(via Spotify)"
    (re.compile(rf"\s*\((?:on |via |from |per |source: )?{_SRCS}\)", re.I), ""),
    # Sentence-initial "According to Last.fm's data, the…" -> "The…"
    (re.compile(rf"(^|(?<=[.!?]\s))(?:According to|Per|Based on|Judging by) (?:the )?{_SRCS}(?:'s)?(?: {_KIND})?,\s*(\w)", re.I),
     lambda m: m.group(1) + m.group(2).upper()),
    # Parenthetical "…, according to Spotify data, …" -> "…"
    (re.compile(rf",\s*(?:according to|as measured (?:on|by)|as tracked (?:on|by)|per) (?:the )?{_SRCS}(?:'s)?(?: {_KIND})?,", re.I), ""),
    # Trailing "… according to Last.fm." / "…, per Spotify"
    (re.compile(rf",?\s*(?:according to|as measured (?:on|by)|as tracked (?:on|by)|per) (?:the )?{_SRCS}(?:'s)?(?: {_KIND})?(?=[\s.;:!?)]|$)", re.I), ""),
    # "…, and on Deezer" / "or via Spotify"
    (re.compile(rf",?\s+(?:and|or)\s+(?:on|via|from|across|in|at)\s+(?:the )?{_SRCS}(?=[\s,.;:!?)]|$)", re.I), ""),
    # "Last.fm listeners" / "Spotify's popularity score" -> "listeners" / "popularity score"
    # (capitalised again when the source name opened the sentence)
    (re.compile(rf"(^|(?<=[.!?]\s)|(?<=\n))?\b{_SRCS}(?:'s)?\s+({_KIND})\b", re.I),
     lambda m: (m.group(2)[0].upper() + m.group(2)[1:]) if m.group(1) is not None else m.group(2)),
    # "tracks on Last.fm reveal" / "streams across Spotify and Deezer,"
    (re.compile(rf"\s+(?:on|via|from|across|in|at) (?:the )?{_SRCS}(?:'s)?(?: {_KIND})?(?=[\s,.;:!?)]|$)", re.I), ""),
]


def strip_source_mentions(text):
    """Remove data-source names (Last.fm, Spotify, Deezer, MusicBrainz) from
    AI-generated copy without breaking the sentence. Safe on falsy input."""
    if not text:
        return text
    out = text
    for pattern, repl in _SOURCE_PATTERNS:
        out = pattern.sub(repl, out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"[ \t]+([,.;:!?])", r"\1", out)
    out = re.sub(r",\s*([.;:!?])", r"\1", out)
    out = re.sub(r",\s*,", ",", out)
    return out


def clean_ai_text(text):
    """Every rule AI copy must follow on Waveline: no em dashes, no source names."""
    return strip_source_mentions(strip_em_dashes(text))

import os
import json
import logging
import requests
import psycopg2
import anthropic
from dotenv import load_dotenv

from db import get_db_connection
from text_clean import strip_em_dashes

load_dotenv()

LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")
BASE_URL = "http://ws.audioscrobbler.com/2.0/"
# Deliberately NOT constructed here: Anthropic is optional enrichment (see
# run_pipeline()), so building the client is deferred to
# _get_anthropic_client(), called only from inside analyse_with_claude().
# A module-level client would tie import-time success of this whole module
# to Anthropic configuration being valid — exactly what this hotfix removes.

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("pipeline.log"),
        logging.StreamHandler()
    ]
)

def get_top_tracks(artist_name):
    """Fetch top tracks for a given artist from Last.fm"""
    try:
        params = {
            "method": "artist.gettoptracks",
            "artist": artist_name,
            "api_key": LASTFM_API_KEY,
            "format": "json",
            "limit": 5
        }
        response = requests.get(BASE_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if "error" in data:
            raise ValueError(f"Last.fm API error: {data['message']}")

        if "toptracks" not in data or "track" not in data["toptracks"]:
            raise ValueError(f"No track data found for artist: {artist_name}")

        tracks = data["toptracks"]["track"]
        logging.info(f"Fetched {len(tracks)} tracks for {artist_name}")
        return tracks

    except requests.exceptions.Timeout:
        logging.error(f"Last.fm API timed out for {artist_name}")
        raise
    except requests.exceptions.RequestException as e:
        logging.error(f"Last.fm API request failed: {e}")
        raise

def _get_anthropic_client():
    """Construct the Anthropic client lazily, at call time, not at module
    import — so a missing/invalid key fails inside analyse_with_claude(),
    exactly where run_pipeline() already treats Claude as optional, rather
    than at `import pipeline` (which would otherwise risk the whole app
    failing to start over an optional-enrichment credential)."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Anthropic API key is not configured")
    return anthropic.Anthropic(api_key=api_key)


def analyse_with_claude(artist_name, tracks):
    """Send track data to Claude for analysis.

    Raises on failure — callers decide whether that's fatal. run_pipeline()
    treats this as optional enrichment; nothing here should assume it's the
    only caller."""
    client = _get_anthropic_client()
    track_list = "\n".join([
        f"{i+1}. {t['name']}: {t['playcount']} plays"
        for i, t in enumerate(tracks)
    ])

    prompt = f"""You are a music analyst. Here are the top 5 most played tracks by {artist_name} on Last.fm:

{track_list}

Please give me:
1. A brief analysis of what these tracks reveal about {artist_name}'s appeal
2. What production or songwriting patterns might explain their popularity
3. One recommendation for a similar artist someone might enjoy

Write in plain prose only. No markdown, no headers, no bullet points, no bold or italic formatting. Just clean paragraphs. Do not use em dashes (the "—" character); use commas, colons or separate sentences instead."""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    logging.info(f"Claude analysis completed for {artist_name}")
    # Belt and suspenders: the prompt already asks Claude not to use em
    # dashes, but that's a request, not a guarantee. Enforce it
    # deterministically before this ever reaches the database.
    return strip_em_dashes(message.content[0].text)


def _log_claude_failure(artist_name, exc):
    """Log enough to diagnose an Anthropic outage (insufficient credits vs
    rate limit vs timeout vs provider/server failure) without ever risking
    a leaked secret, prompt, generated insight, or raw request/response
    payload. Deliberately never logs str(exc) or exc.body — the SDK's own
    error message/body can echo request details we don't want in logs."""
    status_code = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    request_id = None
    if response is not None:
        try:
            request_id = response.headers.get("request-id") or response.headers.get("x-request-id")
        except Exception:
            request_id = None
    logging.error(
        "Claude analysis unavailable for %r: provider=anthropic error=%s status_code=%s request_id=%s",
        artist_name, type(exc).__name__, status_code, request_id,
    )

def save_to_db(artist_name, tracks, insight, user_id=None):
    """Save search results to PostgreSQL, optionally attributed to a user."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO searches (artist_name, top_tracks, claude_insight, user_id) VALUES (%s, %s, %s, %s)",
            (artist_name, json.dumps(tracks), insight, user_id)
        )
        conn.commit()
        cur.close()
        logging.info(f"Saved {artist_name} to database")
    except psycopg2.Error as e:
        logging.error(f"Database error saving {artist_name}: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

def run_pipeline(artist_name, user_id=None):
    """Run the full pipeline for a given artist, optionally attributed to a
    user.

    Last.fm (the core track fetch) and the database save are CORE — a
    failure in either still raises and aborts the pipeline, exactly as
    before. Claude analysis is OPTIONAL enrichment: if the Anthropic
    account is out of credits, rate-limited, or otherwise unavailable, the
    search still succeeds with an empty insight ("" — the searches table's
    claude_insight column is NOT NULL with no default, so empty string is
    the schema-compatible "unavailable" representation, never None) rather
    than failing the whole search. Claude is attempted at most once; there
    is no retry loop.
    """
    logging.info(f"Pipeline started for: {artist_name}")
    tracks = get_top_tracks(artist_name)  # CORE — exceptions propagate

    try:
        insight = analyse_with_claude(artist_name, tracks)
    except Exception as e:
        _log_claude_failure(artist_name, e)
        insight = ""

    save_to_db(artist_name, tracks, insight, user_id=user_id)  # CORE — exceptions propagate

    logging.info(
        f"Pipeline completed for: {artist_name} "
        f"(AI insight: {'generated' if insight else 'unavailable'})"
    )
    return insight
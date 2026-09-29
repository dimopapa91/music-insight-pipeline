"""Durable artist photo store (29 Sep 2026).

Artist photos used to live only in process memory, so every deploy started
from nothing and had to re-ask Deezer for every artist at once. When Deezer
started answering the server with HTTP 403 (and Spotify with 429), every
photo on the site disappeared, including artists we had shown for weeks.

Now every photo found is also written to Postgres. A lookup reads the table
first (fresh rows mean no Deezer call at all), and when the providers fail
the last stored photo is shown instead of a blank tile. All functions are
best-effort: a database problem never breaks a page.
"""

import logging
import time

from db import db_cursor

logger = logging.getLogger(__name__)

FRESH_SECONDS = 30 * 86400   # a stored photo is trusted for 30 days before re-checking

SCHEMA = """
    CREATE TABLE IF NOT EXISTS artist_images (
        name_key   VARCHAR(255) PRIMARY KEY,
        image      TEXT NOT NULL,
        nb_fan     INTEGER NOT NULL DEFAULT 0,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
"""


def _key(name):
    return (name or "").strip().lower()[:255]


def ensure_schema():
    try:
        with db_cursor(commit=True) as cur:
            cur.execute(SCHEMA)
    except Exception:
        logger.warning("artist_images schema not ensured", exc_info=True)


def load(name):
    """{"image", "nb_fan", "fresh": bool} for a stored photo, or None."""
    key = _key(name)
    if not key:
        return None
    try:
        with db_cursor() as cur:
            cur.execute("SELECT image, nb_fan, EXTRACT(EPOCH FROM (NOW() - updated_at)) "
                        "FROM artist_images WHERE name_key = %s", (key,))
            row = cur.fetchone()
    except Exception:
        return None
    if not row or not row[0]:
        return None
    age = float(row[2] or 0)
    return {"image": row[0], "nb_fan": int(row[1] or 0), "fresh": age < FRESH_SECONDS}


def save(name, image, nb_fan=0):
    """Remember a photo that a provider really returned. Empty images are
    never stored, so a failure can't erase a good photo."""
    key = _key(name)
    if not key or not image or not str(image).startswith("https://"):
        return
    try:
        with db_cursor(commit=True) as cur:
            cur.execute("""
                INSERT INTO artist_images (name_key, image, nb_fan, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (name_key) DO UPDATE
                SET image = EXCLUDED.image, nb_fan = EXCLUDED.nb_fan, updated_at = NOW()
            """, (key, image, int(nb_fan or 0)))
    except Exception:
        logger.warning("artist_images save failed", exc_info=True)


# ── provider cool-down ──────────────────────────────────────────────
#
# When a provider refuses us (Deezer 403/429 or its quota error, Spotify 429),
# hammering it on every page view keeps the block alive. Pause all calls to
# that provider for a while instead and serve stored photos meanwhile.

_paused_until = {}


def pause(provider, seconds):
    seconds = max(30, min(int(seconds or 0), 3600))
    until = time.time() + seconds
    if until > _paused_until.get(provider, 0):
        _paused_until[provider] = until
        logger.warning("%s paused for %ss after being refused", provider, seconds)


def paused(provider):
    return time.time() < _paused_until.get(provider, 0)


def reset_pauses():
    _paused_until.clear()

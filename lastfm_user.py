"""Connect Last.fm (27 Sep 2026): build the Taste profile from what someone
actually listens to, not only from what they searched on Waveline.

Only a public Last.fm username is stored (no OAuth, no password, no token):
Last.fm's user.getTopArtists is public for any account. Everything here is
cached in memory and never raises into a request.
"""

import re
import time
import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import requests as http_requests

from db import db_cursor
from services import LASTFM_API_KEY, LASTFM_BASE, get_similar_artists, get_artist_photos

logger = logging.getLogger(__name__)

# Last.fm usernames: 2-15 chars, start with a letter, then letters, digits,
# "_" or "-".
USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,14}$")

PERIODS = {
    "7day": "Last 7 days",
    "1month": "Last month",
    "12month": "Last 12 months",
    "overall": "All time",
}
DEFAULT_PERIOD = "1month"

TOP_ARTISTS = 20          # fetched per period
GRID_ARTISTS = 10         # shown with photos
GENRE_SOURCE_ARTISTS = 12 # artists whose tags feed the genre chart
GENRES_SHOWN = 8
RECS_FROM = 5             # recommendations come from the top N artists
RECS_SHOWN = 6

# Tags that describe the listener or the record, not the sound.
_NOISE_TAGS = {
    "seen live", "favorites", "favourite", "favourites", "favorite", "albums i own",
    "my favorite", "love", "loved", "awesome", "beautiful", "cool", "spotify",
    "under 2000 listeners", "all", "music", "male vocalists", "female vocalists",
    "british", "american", "uk", "usa", "english",
}
_YEAR_RE = re.compile(r"^\d{2,4}s?$")

SCHEMA = """
    CREATE TABLE IF NOT EXISTS lastfm_links (
        user_id      INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        username     VARCHAR(32) NOT NULL,
        connected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
"""


def ensure_schema():
    try:
        with db_cursor(commit=True) as cur:
            cur.execute(SCHEMA)
    except Exception as e:
        logger.error("lastfm_links schema failed: %s", type(e).__name__)


# ── link storage ────────────────────────────────────────────────────

def get_link(user_id):
    try:
        with db_cursor() as cur:
            cur.execute("SELECT username FROM lastfm_links WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            return row[0] if row else None
    except Exception:
        return None


def set_link(user_id, username):
    with db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO lastfm_links (user_id, username) VALUES (%s, %s)
            ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username,
                                                connected_at = CURRENT_TIMESTAMP
        """, (user_id, username))


def remove_link(user_id):
    with db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM lastfm_links WHERE user_id = %s", (user_id,))


# ── Last.fm calls (cached) ──────────────────────────────────────────

_cache = {}


def _cached(key, ttl, fn):
    entry = _cache.get(key)
    if entry and time.time() - entry["at"] < ttl:
        return entry["data"]
    data = fn()
    if data is not None:
        _cache[key] = {"data": data, "at": time.time()}
    return data


def _call(params, timeout=6):
    params = dict(params, api_key=LASTFM_API_KEY, format="json")
    resp = http_requests.get(LASTFM_BASE, params=params, timeout=timeout)
    return resp.json()


def validate_username(username):
    """Canonical Last.fm username if the account exists, else None."""
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        return None
    try:
        data = _call({"method": "user.getInfo", "user": username})
    except Exception:
        return None
    name = (data.get("user") or {}).get("name")
    return name or None


def get_top_artists(username, period=DEFAULT_PERIOD, limit=TOP_ARTISTS):
    """[{"name", "plays"}] for the period, most played first. [] on failure."""
    if period not in PERIODS:
        period = DEFAULT_PERIOD

    def fetch():
        try:
            data = _call({"method": "user.getTopArtists", "user": username,
                          "period": period, "limit": limit})
        except Exception:
            return None   # not cached: retried next time
        items = (data.get("topartists") or {}).get("artist") or []
        out = []
        for a in items:
            try:
                out.append({"name": a["name"], "plays": int(a.get("playcount", 0))})
            except (KeyError, TypeError, ValueError):
                continue
        return out

    return _cached(("top", username.lower(), period, limit), 1800, fetch) or []


def clean_tags(raw_tags):
    tags = []
    for t in raw_tags:
        name = (t.get("name") or "").strip().lower()
        if not name or name in _NOISE_TAGS or _YEAR_RE.match(name) or len(name) > 24:
            continue
        tags.append((name, int(t.get("count", 0) or 0)))
    return tags[:5]


def get_artist_tags(name):
    def fetch():
        try:
            data = _call({"method": "artist.getTopTags", "artist": name}, timeout=5)
        except Exception:
            return None
        return clean_tags((data.get("toptags") or {}).get("tag") or [])

    return _cached(("tags", name.strip().lower()), 86400, fetch) or []


# ── the taste model ─────────────────────────────────────────────────

def genre_weights(top, tags_by_artist, shown=GENRES_SHOWN):
    """Genres weighted by how much each artist is played and how strongly
    Last.fm tags them. Returns [{"name", "share"}] with shares summing to 100."""
    score = Counter()
    for a in top:
        for tag, strength in tags_by_artist.get(a["name"], []):
            score[tag] += a["plays"] * (strength or 1)
    if not score:
        return []
    best = score.most_common(shown)
    total = sum(v for _, v in best) or 1
    return [{"name": n, "share": round(100 * v / total)} for n, v in best]


def recommendations(top, similar_by_artist, shown=RECS_SHOWN):
    """Artists similar to several of your favourites that you don't already
    play, most-recommended first."""
    known = {a["name"].strip().lower() for a in top}
    votes = Counter()
    first_seen = {}
    for i, a in enumerate(top[:RECS_FROM]):
        for j, cand in enumerate(similar_by_artist.get(a["name"], [])):
            key = cand.strip().lower()
            if key in known:
                continue
            votes[key] += 1
            first_seen.setdefault(key, (i, j, cand))
    ranked = sorted(votes, key=lambda k: (-votes[k], first_seen[k][0], first_seen[k][1]))
    return [first_seen[k][2] for k in ranked[:shown]]


def build_taste(username, period=DEFAULT_PERIOD):
    """Everything the Taste page needs for a connected account."""
    top = get_top_artists(username, period)
    if not top:
        return {"top": [], "genres": [], "recs": [], "photos": {}}

    genre_src = top[:GENRE_SOURCE_ARTISTS]
    rec_src = top[:RECS_FROM]
    with ThreadPoolExecutor(max_workers=6) as pool:
        tag_lists = list(pool.map(lambda a: get_artist_tags(a["name"]), genre_src))
        sim_lists = list(pool.map(lambda a: get_similar_artists(a["name"]), rec_src))
    tags_by_artist = {a["name"]: t for a, t in zip(genre_src, tag_lists)}
    similar_by_artist = {a["name"]: s for a, s in zip(rec_src, sim_lists)}

    genres = genre_weights(top, tags_by_artist)
    recs = recommendations(top, similar_by_artist)
    grid = [a["name"] for a in top[:GRID_ARTISTS]]
    photos = get_artist_photos(grid + recs)
    return {"top": top, "genres": genres, "recs": recs, "photos": photos}

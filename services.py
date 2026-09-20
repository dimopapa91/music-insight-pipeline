"""Shared services and helpers for Waveline.

Data-source clients (Spotify, Last.fm, Deezer, MusicBrainz, Ticketmaster,
RSS), the dashboard/compare data builders, and the Jinja display filters all
live here so the view blueprints stay thin. No Flask app or routes in this
module.
"""

import os
import json
import re
import html
import time
import base64
import logging
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime

import requests as http_requests
import markdown as markdown_lib
from markupsafe import Markup

from db import db_cursor

logger = logging.getLogger(__name__)


def _normalize_artist_name(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def artist_names_match(query, result):
    """Does a provider's top search result actually name the artist we asked
    for? Spotify and Deezer both answer a miss with their nearest popular
    match rather than nothing, so taking items[0] blindly hung some unrelated
    star's photo and genres on a niche artist ("BlakeNor" -> "Blake Shelton").
    Compared on letters and digits only, so case, spacing, punctuation and
    accents don't cause false rejections ("Beyoncé" == "beyonce").
    """
    nq, nr = _normalize_artist_name(query), _normalize_artist_name(result)
    return bool(nq) and nq == nr


LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")
LASTFM_BASE = "http://ws.audioscrobbler.com/2.0/"

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
_spotify_token_cache = {}

TICKETMASTER_API_KEY = os.getenv("TICKETMASTER_API_KEY")

# get_spotify_artist() runs on every artist page view. The app is in
# Spotify's Development Mode, whose per-account quota normal traffic can
# exhaust outright (/v1/search then answers 429 QUOTA_EXCEEDED and artist
# Spotify data silently disappears), so results are cached by name here.
# Failures are cached too, for a shorter window: without that, a burst of
# misses during quota exhaustion keeps hammering Spotify and pins the quota
# open instead of letting it recover. The negative cache is the real saver.
_spotify_artist_cache = {}         # name_lower -> {"data": dict, "at": float}
_SPOTIFY_ARTIST_TTL = 86400        # cache SUCCESS for 24h
_SPOTIFY_ARTIST_NEG_TTL = 900      # cache FAILURE/empty for 15m


# ── Jinja display filters ───────────────────────────────────────────

def render_markdown(text):
    """Render AI-generated Markdown text into safe HTML."""
    if not text:
        return Markup("")
    escaped = html.escape(text)
    return Markup(markdown_lib.markdown(escaped, extensions=["nl2br"]))


def markdown_preview(text, length=200):
    """Plain-text teaser derived from rendered Markdown, for truncated previews."""
    if not text:
        return ""
    rendered = markdown_lib.markdown(text)
    plain = re.sub(r"<[^>]+>", "", rendered)
    plain = html.unescape(" ".join(plain.split()))
    return plain[:length]


def avatar_color(username):
    """Deterministic, muted background colour for a monogram avatar tile,
    derived from the username — stable across requests/restarts (no image
    uploads exist). Saturation/lightness are fixed at values that guarantee
    at least 4.5:1 contrast against the site's off-white ink at any hue."""
    name = username or "?"
    h = 0
    for ch in name:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    hue = h % 360
    return f"hsl({hue}, 30%, 30%)"


def artist_titlecase(name):
    """Capitalise the first letter of each word without lowercasing the rest,
    preserving stylised capitalisation like 'BlakeNor' or 'MGMT'."""
    if not name:
        return name
    return " ".join(w[:1].upper() + w[1:] for w in name.split(" "))


def timeago(dt):
    """Relative time string like 'just now', '5m ago', '3h ago', '2d ago'.
    Falls back to an absolute date for anything older than ~30 days.
    DB timestamps are UTC (CURRENT_TIMESTAMP on Railway), so compare to utcnow.
    """
    if not dt or not hasattr(dt, "year"):
        return ""
    import datetime as _dt
    now = _dt.datetime.utcnow()
    delta = now - dt
    secs = delta.total_seconds()
    if secs < 0:
        secs = 0
    if secs < 45:
        return "just now"
    mins = secs / 60
    if mins < 60:
        return f"{int(mins)}m ago"
    hours = mins / 60
    if hours < 24:
        return f"{int(hours)}h ago"
    days = hours / 24
    if days < 30:
        return f"{int(days)}d ago"
    return dt.strftime("%d %b %Y")


# ── Spotify ─────────────────────────────────────────────────────────

def get_spotify_token():
    cached = _spotify_token_cache
    if cached.get("token") and cached.get("expires_at", 0) > time.time():
        return cached["token"]
    try:
        creds = base64.b64encode(f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()).decode()
        resp = http_requests.post("https://accounts.spotify.com/api/token",
            headers={"Authorization": f"Basic {creds}"},
            data={"grant_type": "client_credentials"}, timeout=5)
        data = resp.json()
        if "access_token" in data:
            cached["token"] = data["access_token"]
            cached["expires_at"] = time.time() + data.get("expires_in", 3600) - 60
            return cached["token"]
    except Exception:
        pass
    return None


def get_spotify_artist(artist_name):
    """Returns dict with genres, popularity, followers — or empty dict on failure.

    Cached by lowercased name (see _spotify_artist_cache): successes for 24h,
    failures for 15m, so repeat page views don't re-spend the Development
    Mode quota.
    """
    key = artist_name.strip().lower()
    entry = _spotify_artist_cache.get(key)
    if entry:
        ttl = _SPOTIFY_ARTIST_TTL if entry["data"] else _SPOTIFY_ARTIST_NEG_TTL
        if time.time() - entry["at"] < ttl:
            return entry["data"]

    def _remember(data):
        _spotify_artist_cache[key] = {"data": data, "at": time.time()}
        return data

    token = get_spotify_token()
    if not token:
        return _remember({})
    try:
        resp = http_requests.get("https://api.spotify.com/v1/search",
            headers={"Authorization": f"Bearer {token}"},
            params={"q": artist_name, "type": "artist", "limit": 1}, timeout=5)
        if resp.status_code != 200:
            # 429 is the Development Mode quota running out, which is a
            # different operational problem from a generic API error — worth
            # telling apart in the logs. Never log the token or headers.
            if resp.status_code == 429:
                logger.warning("Spotify search quota exceeded (429) for %r", artist_name)
            else:
                logger.warning("Spotify search HTTP %s for %r", resp.status_code, artist_name)
            return _remember({})
        data = resp.json()
        items = data.get("artists", {}).get("items", [])
        if not items:
            return _remember({})
        a = items[0]
        if not artist_names_match(artist_name, a.get("name", "")):
            # Spotify answered with its nearest popular match, not this
            # artist. Suppress the whole payload rather than showing someone
            # else's popularity, genres and photo — and cache it like any
            # other miss so we don't re-ask on every page view.
            return _remember({})
        return _remember({
            "popularity": a.get("popularity", 0),
            "followers": a.get("followers", {}).get("total", 0),
            "genres": a.get("genres", [])[:4],
            "spotify_url": a.get("external_urls", {}).get("spotify", ""),
            "image": a["images"][1]["url"] if len(a.get("images", [])) > 1 else (a["images"][0]["url"] if a.get("images") else ""),
        })
    except Exception:
        return _remember({})


# ── Last.fm / Deezer discovery ──────────────────────────────────────

# Deezer returns a placeholder URL (the MD5 of an empty string) when an artist
# has no photo, so a non-empty URL is not a reliable signal that an image exists.
DEEZER_BLANK_IMAGE_HASH = "d41d8cd98f00b204e9800998ecf8427e"


def clean_deezer_image(url):
    """Return "" for Deezer's no-photo placeholder so callers can fall back."""
    if not url or DEEZER_BLANK_IMAGE_HASH in url:
        return ""
    return url


# The homepage fired ~17 sequential external calls per render with no
# caching (a Last.fm getSimilar per latest-insight row, more inside
# discovery, then a Deezer lookup per discovered artist at 4s timeout each),
# which is where the ~5s TTFB came from. It's the same data every load, so
# both legs are cached below on the same TTL pattern as
# _spotify_artist_cache: successes held long, failures held briefly so a
# provider outage doesn't get pinned in for a whole day.
_similar_cache = {}            # name_lower -> {"data": list, "at": float}
_SIMILAR_TTL = 86400           # 24h — similar artists change slowly
_SIMILAR_NEG_TTL = 3600        # 1h for empty/failed

_deezer_card_cache = {}        # name_lower -> {"data": dict, "at": float}
_DEEZER_CARD_TTL = 86400
_DEEZER_CARD_NEG_TTL = 3600


def get_similar_artists(artist_name):
    """Last.fm similar artists, cached by lowercased name (see
    _similar_cache). Used by both the homepage and every artist page."""
    key = artist_name.strip().lower()
    entry = _similar_cache.get(key)
    if entry:
        ttl = _SIMILAR_TTL if entry["data"] else _SIMILAR_NEG_TTL
        if time.time() - entry["at"] < ttl:
            return entry["data"]

    def _remember(data):
        _similar_cache[key] = {"data": data, "at": time.time()}
        return data

    try:
        resp = http_requests.get(LASTFM_BASE, params={
            "method": "artist.getSimilar",
            "artist": artist_name,
            "api_key": LASTFM_API_KEY,
            "format": "json",
            "limit": 5
        }, timeout=5)
        data = resp.json()
        if "similarartists" in data and "artist" in data["similarartists"]:
            return _remember([a["name"] for a in data["similarartists"]["artist"][:5]])
    except Exception:
        pass
    return _remember([])


def _deezer_artist_card(name):
    """Deezer artist card for the discovery strip, cached by lowercased name.

    An artist Deezer simply has no photo for still returns a real answer, so
    the empty-image card is cached under the success TTL; only a transport
    failure (non-200 body/exception) is treated as negative and retried
    sooner.
    """
    key = name.strip().lower()
    entry = _deezer_card_cache.get(key)
    if entry:
        # "ok" rides on the entry, not the card, so the returned dict stays
        # exactly {name, image, nb_fan} for the template.
        ttl = _DEEZER_CARD_TTL if entry["ok"] else _DEEZER_CARD_NEG_TTL
        if time.time() - entry["at"] < ttl:
            return entry["data"]

    def _remember(card, ok):
        _deezer_card_cache[key] = {"data": card, "at": time.time(), "ok": ok}
        return card

    try:
        resp = http_requests.get(
            "https://api.deezer.com/search/artist",
            params={"q": name, "limit": 1},
            timeout=4
        )
        data = resp.json()
        if data.get("total", 0) > 0:
            d = data["data"][0]
            return _remember({
                "name": d.get("name", name),
                "image": clean_deezer_image(d.get("picture_medium", "")),
                "nb_fan": d.get("nb_fan", 0)
            }, True)
        # A genuine "Deezer knows nothing about this artist" — a real answer,
        # not a failure, so it keeps the long TTL.
        return _remember({"name": name, "image": "", "nb_fan": 0}, True)
    except Exception:
        return _remember({"name": name, "image": "", "nb_fan": 0}, False)


def get_discovery_artists(searched_artists):
    similar = []
    seen = set()
    for artist in searched_artists[:4]:
        for s in get_similar_artists(artist):
            if s not in seen:
                seen.add(s)
                similar.append(s)

    already = set(a.lower() for a in searched_artists)
    new_artists = [a for a in similar if a.lower() not in already][:8]

    return [_deezer_artist_card(artist) for artist in new_artists]


# ── MBID-anchored artist media ──────────────────────────────────────
#
# Searching a provider by name and taking the top result can't survive a
# name collision: two different artists genuinely share a name and the
# search returns whichever is more popular. artist_names_match() catches a
# wrong *name*, but both candidates here have the right one. MusicBrainz
# solves it properly — Last.fm hands us the artist's MBID, MusicBrainz maps
# that MBID to the artist's own official Spotify/Deezer URLs, and we fetch
# those exact ids. Name search stays only as the fallback for artists with
# no MBID or no linked URLs.

# MusicBrainz rejects requests without a descriptive User-Agent (403), so
# this identifies the app and a contact point, as their policy requires.
MUSICBRAINZ_USER_AGENT = "Waveline/1.0 ( https://wearewaveline.com )"

_SPOTIFY_ARTIST_URL_RE = re.compile(r"open\.spotify\.com/artist/([A-Za-z0-9]+)")
_DEEZER_ARTIST_URL_RE = re.compile(r"deezer\.com/(?:[a-z]{2}/)?artist/(\d+)")

_mb_links_cache = {}           # mbid -> {"data": dict, "at": float}
_MB_LINKS_TTL = 604800         # 7d — an artist's official links rarely move
_MB_LINKS_NEG_TTL = 86400      # 1d for a failed lookup

_spotify_by_id_cache = {}      # spotify_id -> {"data": dict, "at": float}
_deezer_by_id_cache = {}       # deezer_id -> {"data": dict, "at": float, "ok": bool}


def get_musicbrainz_links(mbid):
    """Map a MusicBrainz artist id to that artist's own Spotify/Deezer ids.

    Returns {"spotify_id": str|None, "deezer_id": str|None} — a real answer
    even when the artist has neither link — or {} when the lookup itself
    failed, which is the only case retried on the short TTL.
    """
    if not mbid:
        return {}

    entry = _mb_links_cache.get(mbid)
    if entry:
        ttl = _MB_LINKS_TTL if entry["data"] else _MB_LINKS_NEG_TTL
        if time.time() - entry["at"] < ttl:
            return entry["data"]

    def _remember(data):
        _mb_links_cache[mbid] = {"data": data, "at": time.time()}
        return data

    try:
        resp = http_requests.get(
            f"https://musicbrainz.org/ws/2/artist/{mbid}",
            params={"inc": "url-rels", "fmt": "json"},
            headers={"User-Agent": MUSICBRAINZ_USER_AGENT},
            timeout=6,
        )
        if resp.status_code != 200:
            logger.warning("MusicBrainz HTTP %s for mbid %r", resp.status_code, mbid)
            return _remember({})

        spotify_id = deezer_id = None
        # Scan every relation's URL rather than trusting the relation-type
        # label: the same streaming link shows up under "free streaming",
        # "streaming", or "purchase for download" depending on the artist.
        for relation in resp.json().get("relations", []):
            resource = (relation.get("url") or {}).get("resource", "")
            if not spotify_id:
                m = _SPOTIFY_ARTIST_URL_RE.search(resource)
                if m:
                    spotify_id = m.group(1)
            if not deezer_id:
                m = _DEEZER_ARTIST_URL_RE.search(resource)
                if m:
                    deezer_id = m.group(1)
        return _remember({"spotify_id": spotify_id, "deezer_id": deezer_id})
    except Exception as e:
        logger.warning("MusicBrainz lookup failed for mbid %r (%s)", mbid, type(e).__name__)
        return _remember({})


def get_spotify_artist_by_id(spotify_id):
    """Exact Spotify artist fetch — no search, so no name guard is needed.
    Same dict shape as get_spotify_artist(), or {} on failure."""
    if not spotify_id:
        return {}

    entry = _spotify_by_id_cache.get(spotify_id)
    if entry:
        ttl = _SPOTIFY_ARTIST_TTL if entry["data"] else _SPOTIFY_ARTIST_NEG_TTL
        if time.time() - entry["at"] < ttl:
            return entry["data"]

    def _remember(data):
        _spotify_by_id_cache[spotify_id] = {"data": data, "at": time.time()}
        return data

    token = get_spotify_token()
    if not token:
        return _remember({})
    try:
        resp = http_requests.get(
            f"https://api.spotify.com/v1/artists/{spotify_id}",
            headers={"Authorization": f"Bearer {token}"}, timeout=5)
        if resp.status_code != 200:
            # Never log the token. 429 is the Development Mode quota, which
            # is a different problem from a generic API error.
            if resp.status_code == 429:
                logger.warning("Spotify quota exceeded (429) for artist id %r", spotify_id)
            else:
                logger.warning("Spotify artist HTTP %s for id %r", resp.status_code, spotify_id)
            return _remember({})
        a = resp.json()
        images = a.get("images", [])
        return _remember({
            "popularity": a.get("popularity", 0),
            "followers": a.get("followers", {}).get("total", 0),
            "genres": a.get("genres", [])[:4],
            "spotify_url": a.get("external_urls", {}).get("spotify", ""),
            "image": images[1]["url"] if len(images) > 1 else (images[0]["url"] if images else ""),
        })
    except Exception:
        return _remember({})


def get_deezer_artist_by_id(deezer_id):
    """Exact Deezer artist fetch. Returns {"image": str, "nb_fan": int} —
    empty/zero on failure."""
    empty = {"image": "", "nb_fan": 0}
    if not deezer_id:
        return empty

    entry = _deezer_by_id_cache.get(deezer_id)
    if entry:
        ttl = _DEEZER_CARD_TTL if entry["ok"] else _DEEZER_CARD_NEG_TTL
        if time.time() - entry["at"] < ttl:
            return entry["data"]

    def _remember(data, ok):
        _deezer_by_id_cache[deezer_id] = {"data": data, "at": time.time(), "ok": ok}
        return data

    try:
        resp = http_requests.get(f"https://api.deezer.com/artist/{deezer_id}", timeout=5)
        if resp.status_code != 200:
            logger.warning("Deezer artist HTTP %s for id %r", resp.status_code, deezer_id)
            return _remember(empty, False)
        data = resp.json()
        # Deezer reports a bad id in the body with HTTP 200, so the status
        # code alone isn't enough to tell success from failure here.
        if data.get("error"):
            logger.warning("Deezer artist id %r returned an error body", deezer_id)
            return _remember(empty, False)
        return _remember({
            "image": clean_deezer_image(data.get("picture_medium", "")),
            "nb_fan": data.get("nb_fan", 0),
        }, True)
    except Exception:
        return _remember(empty, False)


def get_deezer_artist_by_name(name):
    """Name-search fallback for Deezer media, guarded by artist_names_match
    so a near-miss can't hand over the wrong artist's photo."""
    try:
        resp = http_requests.get("https://api.deezer.com/search/artist",
            params={"q": name, "limit": 1}, timeout=4)
        d = resp.json()
        if d.get("total", 0) > 0 and artist_names_match(name, d["data"][0].get("name", "")):
            return {
                "image": clean_deezer_image(d["data"][0].get("picture_medium", "")),
                "nb_fan": d["data"][0].get("nb_fan", 0),
            }
    except Exception:
        pass
    return {"image": "", "nb_fan": 0}


def get_artist_media(name, mbid=None):
    """Resolve an artist's Spotify/Deezer media, anchored on their MBID when
    one is available. Returns
    {"spotify": dict, "deezer_image": str, "deezer_fans": int}.

    Linked-id results are trusted outright: if MusicBrainz says this is the
    artist's Spotify page, a name search can't second-guess it. Name search
    only fills a genuine gap (no MBID, no link, or the linked fetch failed).
    """
    links = get_musicbrainz_links(mbid) if mbid else {}

    spotify = get_spotify_artist_by_id(links["spotify_id"]) if links.get("spotify_id") else {}
    if not spotify:
        spotify = get_spotify_artist(name)

    dz = get_deezer_artist_by_id(links["deezer_id"]) if links.get("deezer_id") else {}
    if not dz.get("image"):
        dz = get_deezer_artist_by_name(name)

    return {
        "spotify": spotify,
        "deezer_image": dz.get("image", ""),
        "deezer_fans": dz.get("nb_fan", 0),
    }


# ── Live events (Ticketmaster) ──────────────────────────────────────

_TICKETMASTER_ATTRACTIONS_URL = "https://app.ticketmaster.com/discovery/v2/attractions.json"
_TICKETMASTER_EVENTS_URL = "https://app.ticketmaster.com/discovery/v2/events.json"

_events_cache = {}             # name_lower -> {"data": list, "at": float}
_EVENTS_TTL = 21600            # 6h — tour dates move, but not by the minute
_EVENTS_NEG_TTL = 3600         # 1h for empty/failed


def get_artist_events(name, limit=6):
    """Upcoming Ticketmaster events for an artist, soonest first, or [].

    Anchored on the attraction whose name actually matches: a keyword search
    for "Coldplay" returns tribute acts ("Ultimate Coldplay", "Talk tribute
    Coldplay") ABOVE the real artist, so taking the top hit would advertise
    a tribute band's dates as the artist's own. Same artist_names_match()
    guard used for artwork; no match means no events rather than wrong ones.
    """
    key = name.strip().lower()
    entry = _events_cache.get(key)
    if entry:
        ttl = _EVENTS_TTL if entry["data"] else _EVENTS_NEG_TTL
        if time.time() - entry["at"] < ttl:
            return entry["data"]

    def _remember(data):
        _events_cache[key] = {"data": data, "at": time.time()}
        return data

    if not TICKETMASTER_API_KEY:
        # Same contract as the optional GeoIP database: the feature simply
        # doesn't appear rather than breaking the page.
        logger.info("TICKETMASTER_API_KEY not set — artist events are disabled.")
        return _remember([])

    try:
        resp = http_requests.get(_TICKETMASTER_ATTRACTIONS_URL, params={
            "keyword": name,
            "classificationName": "Music",
            "size": 20,
            "apikey": TICKETMASTER_API_KEY,
        }, timeout=8)
        if resp.status_code != 200:
            # Never log the params: they carry the API key.
            logger.warning("Ticketmaster attractions HTTP %s for %r", resp.status_code, name)
            return _remember([])

        attractions = resp.json().get("_embedded", {}).get("attractions", [])
        match = next((a for a in attractions if artist_names_match(name, a.get("name", ""))), None)
        if not match:
            return _remember([])

        # The attraction already carries its upcoming-event count, so a
        # zero here saves the second request entirely.
        if (match.get("upcomingEvents") or {}).get("_total") == 0:
            return _remember([])

        resp = http_requests.get(_TICKETMASTER_EVENTS_URL, params={
            "attractionId": match.get("id"),
            "sort": "date,asc",
            "size": limit,
            "apikey": TICKETMASTER_API_KEY,
        }, timeout=8)
        if resp.status_code != 200:
            logger.warning("Ticketmaster events HTTP %s for %r", resp.status_code, name)
            return _remember([])

        events = []
        for ev in resp.json().get("_embedded", {}).get("events", []):
            venue = (ev.get("_embedded", {}).get("venues") or [{}])[0]
            date_raw = ev.get("dates", {}).get("start", {}).get("localDate", "")
            try:
                display_date = datetime.strptime(date_raw, "%Y-%m-%d").strftime("%d %b %Y")
            except Exception:
                display_date = date_raw
            events.append({
                "date": display_date,
                "venue": venue.get("name", ""),
                "city": (venue.get("city") or {}).get("name", ""),
                "country": (venue.get("country") or {}).get("name", ""),
                "url": ev.get("url", ""),
                "title": ev.get("name", ""),
            })
        return _remember(events)
    except Exception as e:
        logger.warning("Ticketmaster lookup failed for %r (%s)", name, type(e).__name__)
        return _remember([])


# ── Dashboard + compare data builders ───────────────────────────────

def _latest_nonempty_insight(artist_name):
    """Most recent non-empty claude_insight for this artist from an earlier
    search — used only as a fallback when the newest row's insight is empty
    (Claude was unavailable at analysis time), so a perfectly good older
    insight isn't hidden just because the latest attempt happened to fail.
    One extra, single-artist query; never raises."""
    try:
        with db_cursor() as cur:
            cur.execute(
                "SELECT claude_insight FROM searches "
                "WHERE LOWER(artist_name) = LOWER(%s) AND claude_insight <> '' "
                "ORDER BY searched_at DESC LIMIT 1",
                (artist_name,),
            )
            row = cur.fetchone()
            return row[0] if row else ""
    except Exception:
        return ""


def resolve_insight(artist_name, current_insight):
    """(insight, is_reused) for display. current_insight is the newest
    search row's claude_insight for this artist (possibly "" or None if
    Claude failed at analysis time — the column itself is NOT NULL with no
    default, so "" is the only representation that should ever reach here,
    but callers may pass a raw DB value defensively). Falls back to the
    most recent older non-empty insight for the same artist when the
    current one is empty, so callers never need to special-case None."""
    current_insight = current_insight or ""
    if current_insight:
        return current_insight, False
    fallback = _latest_nonempty_insight(artist_name)
    return fallback, bool(fallback)


# The homepage's whole payload is global, not per-user, so every visitor
# rebuilds the identical thing — several DB aggregates plus the external
# discovery calls above. A short TTL is the single biggest lever on the ~5s
# TTFB; it's deliberately brief so a new search still surfaces quickly.
_dashboard_cache = {"data": None, "at": 0}
_DASHBOARD_TTL = 180           # 3 minutes


def clear_dashboard_cache():
    _dashboard_cache["data"] = None
    _dashboard_cache["at"] = 0


def get_dashboard_data():
    if _dashboard_cache["data"] is not None and time.time() - _dashboard_cache["at"] < _DASHBOARD_TTL:
        return _dashboard_cache["data"]

    with db_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM searches")
        total_searches = cur.fetchone()[0]

        cur.execute("SELECT COUNT(DISTINCT artist_name) FROM searches")
        unique_artists = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM searches WHERE DATE(searched_at) = CURRENT_DATE")
        searches_today = cur.fetchone()[0]

        cur.execute("SELECT artist_name, top_tracks FROM searches")
        rows = cur.fetchall()
        artist_avg = {}
        for artist_name, top_tracks in rows:
            tracks = top_tracks if isinstance(top_tracks, list) else json.loads(top_tracks)
            plays = [int(t["playcount"]) for t in tracks]
            avg = sum(plays) // len(plays)
            if artist_name not in artist_avg:
                artist_avg[artist_name] = []
            artist_avg[artist_name].append(avg)
        final_avgs = {k: sum(v) // len(v) for k, v in artist_avg.items()}
        sorted_avgs = sorted(final_avgs.items(), key=lambda x: x[1], reverse=True)
        max_avg = sorted_avgs[0][1] if sorted_avgs else 1
        artist_plays = [(a, avg, max_avg) for a, avg in sorted_avgs]

        cur.execute("""
            SELECT * FROM (
                SELECT DISTINCT ON (artist_name) artist_name, claude_insight, searched_at, top_tracks
                FROM searches
                ORDER BY artist_name, searched_at DESC
            ) sub
            ORDER BY searched_at DESC LIMIT 5
        """)

        class Row:
            def __init__(self, r):
                self.artist = r[0]
                self.insight, self.insight_is_reused = resolve_insight(r[0], r[1])
                self.searched_at = r[2]
                tracks_raw = r[3] if isinstance(r[3], list) else json.loads(r[3])
                self.top_tracks = [t["name"] for t in tracks_raw[:4]]
                self.similar_artists = get_similar_artists(r[0])

        latest_insights = [Row(r) for r in cur.fetchall()]

        cur.execute("SELECT DISTINCT artist_name FROM searches ORDER BY artist_name")
        all_artists = [r[0] for r in cur.fetchall()]

    # DB connection released before the (slower) external discovery calls
    discovery = get_discovery_artists(all_artists)

    data = total_searches, unique_artists, searches_today, artist_plays, latest_insights, discovery
    _dashboard_cache["data"] = data
    _dashboard_cache["at"] = time.time()
    return data


def get_artist_db(name):
    try:
        with db_cursor() as cur:
            cur.execute("""
                SELECT artist_name, claude_insight, top_tracks FROM searches
                WHERE LOWER(artist_name) = LOWER(%s)
                ORDER BY searched_at DESC LIMIT 1
            """, (name,))
            row = cur.fetchone()
        if not row:
            return None
        tracks_raw = row[2] if isinstance(row[2], list) else json.loads(row[2])
        spotify = get_spotify_artist(row[0])
        insight, insight_is_reused = resolve_insight(row[0], row[1])
        return {
            "name": row[0],
            "insight": insight,
            "insight_is_reused": insight_is_reused,
            "tracks": [t["name"] for t in tracks_raw[:5]],
            "spotify_url": spotify.get("spotify_url", ""),
            "image": spotify.get("image", "")
        }
    except Exception:
        return None


# ── Music news (RSS + Deezer new releases) ──────────────────────────

_news_cache = {"data": None, "fetched_at": 0}

RSS_FEEDS = [
    {"name": "Pitchfork",        "url": "https://pitchfork.com/rss/news/feed.xml",          "color": "#e00"},
    {"name": "NME",              "url": "https://www.nme.com/feed",                          "color": "#ff6900"},
    {"name": "The Guardian",     "url": "https://www.theguardian.com/music/rss",             "color": "#005689"},
    {"name": "Resident Advisor", "url": "https://ra.co/xml/news.xml",                        "color": "#1da0c3"},
]


def fetch_rss(feed):
    try:
        resp = http_requests.get(feed["url"], timeout=6, headers={"User-Agent": "MusicInsightPipeline/1.0"})
        root = ET.fromstring(resp.content)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items = []
        channel = root.find("channel")
        entries = (channel.findall("item") if channel is not None else []) or root.findall("atom:entry", ns)
        for entry in entries[:4]:
            title = (entry.findtext("title") or entry.findtext("atom:title", namespaces=ns) or "").strip()
            link  = (entry.findtext("link")  or entry.findtext("atom:link[@rel='alternate']", namespaces=ns) or "")
            if not link:
                link_el = entry.find("atom:link", ns)
                link = link_el.get("href", "") if link_el is not None else ""
            pub   = (entry.findtext("pubDate") or entry.findtext("atom:published", namespaces=ns) or "")
            if title and link:
                items.append({"title": title.replace("&amp;", "&"), "link": link.strip(), "pub": pub[:16], "source": feed["name"], "color": feed["color"]})
        return items
    except Exception:
        return []


def get_deezer_trending_albums(limit=12):
    """Deezer's albums chart (trending/popular albums), in the same shape
    the news template already renders.

    Not editorial releases: /editorial/0/releases answers HTTP 200 with
    {"data": [], "total": 0} — it's dead — so the strip rendered blank.
    /chart/0/albums returns real data. Chart albums carry no release_date,
    so "date" comes back "" and the template simply omits it. Deezer needs
    no API key (it's already used keyless elsewhere in this module), which
    is why this replaced Spotify's /v1/browse/new-releases — permanently
    403 for this app tier.
    """
    try:
        resp = http_requests.get("https://api.deezer.com/chart/0/albums",
            params={"limit": limit}, timeout=5)
        if resp.status_code != 200:
            # Log for developers; never surface raw provider errors to users.
            logger.warning("Deezer trending albums HTTP %s: %s", resp.status_code, resp.text[:200])
            return []
        albums = resp.json().get("data", [])
        results = []
        for a in albums:
            results.append({
                "name":    a.get("title", ""),
                "artist":  a.get("artist", {}).get("name", ""),
                "image":   a.get("cover_medium", ""),
                "url":     a.get("link", ""),
                "type":    a.get("record_type", "album").capitalize(),
                "date":    a.get("release_date", ""),
            })
        return results
    except Exception as e:
        logger.warning("Deezer trending albums failed: %s", e)
        return []


# Last successful releases, so a transient provider failure doesn't blank the section.
_last_releases = []


def get_news_data():
    """Returns articles + releases + a releases_status ('live' | 'cached' | 'unavailable')
    and the list of sources. Successfully loaded sections stay visible even if one
    provider fails; the whole result is cached for an hour (cleared by refresh)."""
    global _last_releases
    if _news_cache["data"] and time.time() - _news_cache["fetched_at"] < 3600:
        return _news_cache["data"]

    articles = []
    for feed in RSS_FEEDS:
        articles.extend(fetch_rss(feed))

    releases = get_deezer_trending_albums()
    if releases:
        _last_releases = releases
        releases_status = "live"
    elif _last_releases:
        releases = _last_releases      # fall back to last good data
        releases_status = "cached"
    else:
        releases_status = "unavailable"

    data = {
        "articles": articles,
        "releases": releases,
        "releases_status": releases_status,
        "sources": [f["name"] for f in RSS_FEEDS] + ["Deezer"],
    }
    _news_cache["data"] = data
    _news_cache["fetched_at"] = time.time()
    return data


def clear_news_cache():
    _news_cache["data"] = None
    _news_cache["fetched_at"] = 0

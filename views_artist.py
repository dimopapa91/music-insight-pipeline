"""Artist blueprint: single artist profile (/artist/<name>) and comparison (/compare)."""

import os
import json
import time
import logging
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import requests as http_requests
from flask import (Blueprint, render_template, request, redirect, url_for)
from flask_login import current_user

from db import db_cursor
from pipeline import run_pipeline
from rate_limit import limiter
from text_clean import strip_em_dashes, clean_ai_text
from services import (
    get_similar_artists, get_artist_media, get_artist_events, get_artist_db,
    get_artist_photos, record_artist_open,
    artist_titlecase, resolve_insight, LASTFM_BASE, LASTFM_API_KEY,
)

artist_bp = Blueprint("artist", __name__)

# In-memory cache for the /compare AI verdict, keyed on the normalised
# (order-independent) artist pair — mirrors views_taste.py's _taste_cache
# pattern exactly. Without this, comparing the same two artists twice made
# a fresh, uncached Claude call every single time; a crawler (or just two
# curious visitors) requesting the same popular pair repeatedly was pure
# waste. Never cached on failure, so a transient provider error is retried
# on the next request rather than being stuck.
_compare_cache = {}


# Last.fm artist.getInfo (listeners, scrobbles, tags, MBID) used to be
# fetched on every artist page view, uncached, in series with the other
# lookups: the main reason artist pages took 2+ seconds. Cached per
# lowercased name; successes for 6h, failures for 5 min.
_lastfm_info_cache = {}
_LASTFM_INFO_TTL = 6 * 3600
_LASTFM_INFO_NEG_TTL = 300
_EMPTY_LASTFM_INFO = {"listeners": 0, "scrobbles": 0, "tags": [], "mbid": ""}


def _lastfm_artist_info(name):
    key = name.strip().lower()
    entry = _lastfm_info_cache.get(key)
    if entry:
        ttl = _LASTFM_INFO_TTL if entry["ok"] else _LASTFM_INFO_NEG_TTL
        if time.time() - entry["at"] < ttl:
            return entry["data"]
    data, ok = dict(_EMPTY_LASTFM_INFO), False
    try:
        resp = http_requests.get(LASTFM_BASE, params={
            "method": "artist.getInfo",
            "artist": name,
            "api_key": LASTFM_API_KEY,
            "format": "json"
        }, timeout=5)
        artist = resp.json().get("artist", {})
        stats = artist.get("stats", {})
        data = {
            "listeners": int(stats.get("listeners", 0)),
            "scrobbles": int(stats.get("playcount", 0)),
            "tags": [t["name"] for t in artist.get("tags", {}).get("tag", [])[:4]],
            "mbid": artist.get("mbid", ""),
        }
        ok = bool(artist)
    except Exception:
        pass
    _lastfm_info_cache[key] = {"data": data, "at": time.time(), "ok": ok}
    return data


# An analysis older than this gets a "Refresh analysis" button for
# logged-in users (it POSTs to /search, which is rate-limited).
STALE_AFTER_DAYS = 30


def _is_stale(searched_at):
    if not hasattr(searched_at, "date"):
        return False
    import datetime as _dt
    now = _dt.datetime.utcnow() if searched_at.tzinfo is None else _dt.datetime.now(_dt.timezone.utc)
    return (now - searched_at).days >= STALE_AFTER_DAYS


def _safe_int(value):
    """Last.fm play counts arrive as strings; a malformed one counts as 0
    rather than taking the whole artist page down."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _compare_cache_key(name_a, name_b):
    return " :: ".join(sorted([name_a.strip().lower(), name_b.strip().lower()]))


@artist_bp.route("/artist/<path:artist_name>")
@limiter.limit("60 per hour")
def artist_profile(artist_name):
    try:
        with db_cursor() as cur:
            cur.execute("""
                SELECT artist_name, claude_insight, searched_at, top_tracks
                FROM searches
                WHERE LOWER(artist_name) = LOWER(%s)
                ORDER BY searched_at DESC LIMIT 1
            """, (artist_name,))
            row = cur.fetchone()
            if row:
                cur.execute("SELECT COUNT(*) FROM searches WHERE LOWER(artist_name) = LOWER(%s)", (artist_name,))
                search_count = cur.fetchone()[0]
        if not row:
            # A GET never starts paid work (Last.fm + Claude). Before 27 Sep
            # 2026 a logged-in GET of an unanalysed artist ran the whole
            # pipeline, so any link checker, prefetcher or crawler riding a
            # logged-in session triggered paid analyses. Now everyone gets
            # the same calm page with a real 404; logged-in users get an
            # "Analyse" button that POSTs to /search (rate-limited), and
            # anonymous visitors get the sign-up prompt.
            #
            # Real 404, not 200: without an external API call we can't tell
            # a genuine not-yet-searched artist from crawler garbage or a
            # typo, so the URL honestly doesn't resolve to a resource yet,
            # and search engines won't index it.
            return render_template("artist_not_found.html",
                artist_name=artist_titlecase(artist_name),
                raw_name=artist_name.strip(),
                can_analyse=current_user.is_authenticated), 404

        name, insight, last_searched, top_tracks_raw = row
        # Opened from a search (⌘K palette / homepage suggestions): count it
        # as this artist's latest search for the homepage, without paying for
        # a fresh pipeline run. Only ever for artists already in the table.
        if request.args.get("from") == "search":
            record_artist_open(name, current_user.id if current_user.is_authenticated else None)
        # If the newest search's Claude call failed (empty insight), fall
        # back to the most recent older non-empty insight for this artist
        # rather than showing a broken/blank AI section — see pipeline.py's
        # run_pipeline() for why an empty insight can exist at all.
        insight, insight_is_reused = resolve_insight(name, insight)
        tracks_list = top_tracks_raw if isinstance(top_tracks_raw, list) else json.loads(top_tracks_raw)
        # Sorted by plays, most first: the stored order isn't guaranteed to
        # be (Risingson at 6.34M used to sit below Inertia Creeps at 6.20M),
        # and the "top track" stat below reads tracks[0].
        tracks = sorted(
            ({"name": t["name"], "plays": _safe_int(t.get("playcount", 0))} for t in tracks_list),
            key=lambda t: t["plays"], reverse=True,
        )
        top_playcount = f"{tracks[0]['plays']:,}" if tracks else "—"
        avg_plays = f"{sum(t['plays'] for t in tracks) // len(tracks):,}" if tracks else "—"

        # External lookups, in two parallel waves instead of one after
        # another. Wave 1 needs only the name; wave 2 needs the MBID
        # (media) or the similar list (photos) from wave 1.
        with ThreadPoolExecutor(max_workers=3) as pool:
            f_similar = pool.submit(get_similar_artists, name)
            f_info = pool.submit(_lastfm_artist_info, name)
            f_events = pool.submit(get_artist_events, name)
            similar = f_similar.result()
            info = f_info.result()
            events = f_events.result()

        lastfm_listeners = info["listeners"]
        lastfm_scrobbles = info["scrobbles"]
        lastfm_tags = info["tags"]
        mbid = info["mbid"]

        # Spotify + Deezer media, anchored on the MBID when there is one so
        # an artist who shares a name with someone more popular still gets
        # their own photo and stats (see services.get_artist_media).
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_media = pool.submit(get_artist_media, name, mbid)
            f_photos = pool.submit(get_artist_photos, similar) if similar else None
            media = f_media.result()
            similar_photos = f_photos.result() if f_photos else {}
        spotify = media["spotify"]
        deezer_image = media["deezer_image"]
        deezer_fans = media["deezer_fans"]
        if not spotify:
            logging.warning(f"no spotify media for '{name}'")

        return render_template("artist_profile.html",
            artist_name=name,
            insight=insight,
            insight_is_reused=insight_is_reused,
            tracks=tracks,
            similar_artists=similar,
            similar_photos=similar_photos,
            events=events,
            search_count=search_count,
            last_searched=last_searched.strftime("%d %b %Y") if hasattr(last_searched, 'strftime') else str(last_searched),
            insight_is_stale=_is_stale(last_searched),
            top_playcount=top_playcount,
            avg_plays=avg_plays,
            deezer_image=deezer_image,
            deezer_fans=deezer_fans,
            lastfm_listeners=lastfm_listeners,
            lastfm_scrobbles=lastfm_scrobbles,
            lastfm_tags=lastfm_tags,
            spotify=spotify,
            urlencode=quote,
        )
    except Exception:
        return render_template("error.html",
            heading="Something went wrong",
            message="Could not load the artist profile. This might be a temporary database issue."), 500


def _cached_compare_verdict(a_data, b_data):
    """Cached AI verdict for a pair — see _compare_cache above. Returns None
    (not cached) on any provider failure, exactly like the pre-existing
    fallback contract; the template already renders a calm notice for that."""
    key = _compare_cache_key(a_data["name"], b_data["name"])
    if key in _compare_cache:
        return _compare_cache[key]

    import anthropic as _anthropic
    # get_artist_db() already guarantees a string via resolve_insight(),
    # but never make artist AI insight a prerequisite for this page —
    # .get(...) or "" stays safe even if that guarantee ever changes.
    prompt = f"""Compare these two artists:

{a_data['name']} top tracks: {', '.join(a_data['tracks'])}
Insight: {(a_data.get('insight') or '')[:400]}

{b_data['name']} top tracks: {', '.join(b_data['tracks'])}
Insight: {(b_data.get('insight') or '')[:400]}

Write a 2-paragraph comparison in plain prose. Cover: how their sounds and appeal differ, what they share, and which type of listener would prefer each. No markdown, no bullet points. Do not use em dashes (the "—" character); use commas, colons or separate sentences instead. Do not mention where the data comes from and do not name any data platform (Last.fm, Spotify, Deezer, MusicBrainz); talk about the music and the listeners directly."""
    try:
        _client = _anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        msg = _client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=500, messages=[{"role": "user", "content": prompt}])
        # Belt and suspenders: the prompt already asks Claude not to use em
        # dashes, but that's a request, not a guarantee. Enforce it
        # deterministically before this is cached.
        verdict = clean_ai_text(msg.content[0].text)
        _compare_cache[key] = verdict
        return verdict
    except Exception as e:
        status_code = getattr(e, "status_code", None)
        logging.error(
            "Compare verdict unavailable: provider=anthropic error=%s%s",
            type(e).__name__, f" status_code={status_code}" if status_code is not None else "",
        )
        return None  # never cached — a fresh attempt is made next request


@artist_bp.route("/compare")
@limiter.limit("20 per hour")
def compare():
    a = request.args.get("a", "").strip()
    b = request.args.get("b", "").strip()

    a_data = get_artist_db(a) if a else None
    b_data = get_artist_db(b) if b else None

    # Anonymous visitors never trigger a pipeline run here. /compare is a
    # public, unauthenticated GET whose ?a=/?b= values are fully
    # attacker-controlled — auto-fetching for anyone who asks meant up to
    # three Claude calls (two pipeline runs plus the verdict) per novel
    # request, which is the core abuse vector this closes. Logged-in
    # members keep the original auto-fetch behaviour exactly.
    missing_for_anonymous = False
    if current_user.is_authenticated:
        if a and not a_data:
            try:
                run_pipeline(a)
            except Exception:
                pass
            a_data = get_artist_db(a)
        if b and not b_data:
            try:
                run_pipeline(b)
            except Exception:
                pass
            b_data = get_artist_db(b)
    else:
        missing_for_anonymous = bool((a and not a_data) or (b and not b_data))

    verdict = ""
    if a_data and b_data:
        verdict = _cached_compare_verdict(a_data, b_data)

    return render_template("compare.html", a=a, b=b, a_data=a_data, b_data=b_data,
                            verdict=verdict, missing_for_anonymous=missing_for_anonymous)

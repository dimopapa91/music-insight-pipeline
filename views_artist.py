"""Artist blueprint: single artist profile (/artist/<name>) and comparison (/compare)."""

import os
import json
import logging
from urllib.parse import quote

import requests as http_requests
from flask import (Blueprint, render_template, request, redirect, url_for)
from flask_login import current_user

from db import db_cursor
from pipeline import run_pipeline
from rate_limit import limiter
from services import (
    get_similar_artists, get_spotify_artist, get_artist_db, artist_titlecase,
    clean_deezer_image, resolve_insight, LASTFM_BASE, LASTFM_API_KEY,
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
            if not current_user.is_authenticated:
                # This is a public, unauthenticated GET with an
                # attacker-controlled path segment — running the pipeline
                # here for anyone who asks is exactly the cost/abuse vector
                # this closes (a fresh Last.fm + Claude call per novel URL a
                # crawler invents). Turn the dead end into a signup moment
                # instead of silently doing paid work for anonymous traffic.
                #
                # Real 404, not 200: we have no way to tell a genuine
                # not-yet-searched artist apart from crawler garbage/typos
                # without an external API call, which would defeat the whole
                # point of not doing paid work here — so this URL genuinely
                # doesn't resolve to a resource right now, and a 404 status
                # says that honestly while still rendering the same calm,
                # on-brand page instead of a bare error. robots.txt already
                # disallows /artist/ entirely, so this has no SEO downside.
                return render_template("artist_not_found.html",
                    artist_name=artist_titlecase(artist_name)), 404
            # Logged-in: keep the existing behaviour exactly — auto-fetch,
            # then reload the now-populated page.
            try:
                run_pipeline(artist_name)
                return redirect(url_for("artist.artist_profile", artist_name=artist_name))
            except Exception:
                return render_template("error.html",
                    heading=f"Could not load {artist_titlecase(artist_name)}",
                    message="We couldn't fetch this artist from our data sources just now. Check the spelling, or try again in a moment."), 500

        name, insight, last_searched, top_tracks_raw = row
        # If the newest search's Claude call failed (empty insight), fall
        # back to the most recent older non-empty insight for this artist
        # rather than showing a broken/blank AI section — see pipeline.py's
        # run_pipeline() for why an empty insight can exist at all.
        insight, insight_is_reused = resolve_insight(name, insight)
        tracks_list = top_tracks_raw if isinstance(top_tracks_raw, list) else json.loads(top_tracks_raw)
        tracks = [{"name": t["name"], "plays": int(t.get("playcount", 0))} for t in tracks_list]
        top_playcount = f"{tracks[0]['plays']:,}" if tracks else "—"
        avg_plays = f"{sum(t['plays'] for t in tracks) // len(tracks):,}" if tracks else "—"

        similar = get_similar_artists(name)

        # Deezer: image + fans
        deezer_image = ""
        deezer_fans = 0
        try:
            resp = http_requests.get("https://api.deezer.com/search/artist",
                params={"q": name, "limit": 1}, timeout=4)
            d = resp.json()
            if d.get("total", 0) > 0:
                deezer_image = clean_deezer_image(d["data"][0].get("picture_medium", ""))
                deezer_fans = d["data"][0].get("nb_fan", 0)
        except Exception:
            pass

        # Spotify: genres, popularity, followers
        spotify = get_spotify_artist(name)
        if not spotify:
            logging.warning(f"get_spotify_artist() returned empty data for '{name}'")

        # Last.fm: listeners + total scrobbles + top tags
        lastfm_listeners = 0
        lastfm_scrobbles = 0
        lastfm_tags = []
        try:
            resp = http_requests.get(LASTFM_BASE, params={
                "method": "artist.getInfo",
                "artist": name,
                "api_key": LASTFM_API_KEY,
                "format": "json"
            }, timeout=5)
            info = resp.json()
            stats = info.get("artist", {}).get("stats", {})
            lastfm_listeners = int(stats.get("listeners", 0))
            lastfm_scrobbles = int(stats.get("playcount", 0))
            lastfm_tags = [t["name"] for t in info.get("artist", {}).get("tags", {}).get("tag", [])[:4]]
        except Exception:
            pass

        return render_template("artist_profile.html",
            artist_name=name,
            insight=insight,
            insight_is_reused=insight_is_reused,
            tracks=tracks,
            similar_artists=similar,
            search_count=search_count,
            last_searched=last_searched.strftime("%d %b %Y") if hasattr(last_searched, 'strftime') else str(last_searched),
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

Write a 2-paragraph comparison in plain prose. Cover: how their sounds and appeal differ, what they share, and which type of listener would prefer each. No markdown, no bullet points. Do not use em dashes (the "—" character); use commas, colons or separate sentences instead."""
    try:
        _client = _anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        msg = _client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=500, messages=[{"role": "user", "content": prompt}])
        verdict = msg.content[0].text
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

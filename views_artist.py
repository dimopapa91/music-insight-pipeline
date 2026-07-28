"""Artist blueprint: single artist profile (/artist/<name>) and comparison (/compare)."""

import os
import json
import logging
from urllib.parse import quote

import requests as http_requests
from flask import (Blueprint, render_template, request, redirect, url_for)

from db import db_cursor
from pipeline import run_pipeline
from services import (
    get_similar_artists, get_spotify_artist, get_artist_db, artist_titlecase,
    clean_deezer_image, LASTFM_BASE, LASTFM_API_KEY,
)

artist_bp = Blueprint("artist", __name__)


@artist_bp.route("/artist/<path:artist_name>")
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
            # Artist not in DB — run the pipeline automatically, then reload
            try:
                run_pipeline(artist_name)
                return redirect(url_for("artist.artist_profile", artist_name=artist_name))
            except Exception:
                return render_template("error.html",
                    heading=f"Could not load {artist_titlecase(artist_name)}",
                    message="We couldn't fetch this artist from our data sources just now. Check the spelling, or try again in a moment."), 500

        name, insight, last_searched, top_tracks_raw = row
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


@artist_bp.route("/compare")
def compare():
    import anthropic as _anthropic
    a = request.args.get("a", "").strip()
    b = request.args.get("b", "").strip()

    # Auto-fetch any artist not yet in the DB
    if a and not get_artist_db(a):
        try:
            run_pipeline(a)
        except Exception:
            pass
    if b and not get_artist_db(b):
        try:
            run_pipeline(b)
        except Exception:
            pass

    a_data = get_artist_db(a) if a else None
    b_data = get_artist_db(b) if b else None
    verdict = ""
    if a_data and b_data:
        prompt = f"""Compare these two artists:

{a_data['name']} top tracks: {', '.join(a_data['tracks'])}
Insight: {a_data['insight'][:400]}

{b_data['name']} top tracks: {', '.join(b_data['tracks'])}
Insight: {b_data['insight'][:400]}

Write a 2-paragraph comparison in plain prose. Cover: how their sounds and appeal differ, what they share, and which type of listener would prefer each. No markdown, no bullet points. Do not use em dashes (the "—" character); use commas, colons or separate sentences instead."""
        try:
            _client = _anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            msg = _client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=500, messages=[{"role": "user", "content": prompt}])
            verdict = msg.content[0].text
        except Exception:
            verdict = None  # calm fallback in template; no provider detail leaked
    return render_template("compare.html", a=a, b=b, a_data=a_data, b_data=b_data, verdict=verdict)

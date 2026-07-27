"""Artist blueprint: single artist profile (/artist/<name>) and comparison (/compare)."""

import os
import json
import logging
from urllib.parse import quote

import requests as http_requests
from flask import (Blueprint, render_template, render_template_string, request, redirect, url_for)

from db import db_cursor
from pipeline import run_pipeline
from services import (
    get_similar_artists, get_spotify_artist, get_artist_db,
    clean_deezer_image, LASTFM_BASE, LASTFM_API_KEY,
)

artist_bp = Blueprint("artist", __name__)

_ARTIST_ERROR = """
<!DOCTYPE html><html><head><meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>body{font-family:'Space Mono',monospace;background:#f5f5f3;padding:60px 36px;font-size:17px;}
.topbar{background:#111;color:#fff;padding:0 24px;height:44px;display:flex;align-items:center;position:fixed;top:0;left:0;right:0;}
.topbar-brand{font-weight:700;font-size:0.95em;letter-spacing:2px;color:#1da0c3;}
.box{background:#fff;border:1px solid #e8e8e8;border-radius:6px;padding:32px 36px;max-width:500px;margin:60px auto;box-shadow:0 1px 3px rgba(0,0,0,0.05);}
h2{font-size:1.05em;color:#111;margin-bottom:12px;line-height:1.2;}
p{font-size:0.8em;color:#555;line-height:1.7;}
a{color:#1da0c3;font-size:0.8em;}
</style></head><body>
<div class="topbar"><span class="topbar-brand">WAVELINE</span></div>
<div class="box">
<h2>Could not load {{ name | titlecase }}</h2>
<p>{{ error }}</p>
<a href="/">← Back to dashboard</a>
</div></body></html>
"""


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
            except Exception as e:
                return render_template_string(_ARTIST_ERROR, name=artist_name, error=str(e)), 500

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
        return render_template_string("""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Error — Waveline</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>*{box-sizing:border-box;}body{font-family:'Space Mono',monospace;background:#f5f5f3;margin:0;padding-top:60px;}
.topbar{background:#000;height:48px;display:flex;align-items:center;justify-content:space-between;padding:0 24px;position:fixed;top:0;left:0;right:0;z-index:100;}
.topbar-brand{font-weight:700;font-size:0.85em;letter-spacing:2px;color:#fff;text-decoration:none;}
.topbar-right{display:flex;gap:20px;align-items:center;}
.topbar-link{font-size:0.62em;letter-spacing:2px;text-transform:uppercase;color:#1da0c3;text-decoration:none;}
.box{background:#fff;border:1px solid #e8e8e8;padding:32px;max-width:520px;margin:60px auto;}
h2{font-size:1em;color:#111;margin:0 0 12px;}
p{font-size:0.8em;color:#666;line-height:1.7;}
a.back{color:#1da0c3;font-size:0.8em;text-decoration:none;}
</style></head><body>
<div class="topbar">
  <a href="/" class="topbar-brand">WAVELINE</a>
  <div class="topbar-right">
    <a href="/news" class="topbar-link">News</a>
    <a href="/compare" class="topbar-link">Compare</a>
    <a href="/profile" class="topbar-link">Taste Profile</a>
  </div>
</div>
<div class="box">
<h2>Something went wrong</h2>
<p>Could not load the artist profile. This might be a temporary database issue.</p>
<a href="/" class="back">← Back to dashboard</a>
</div></body></html>
"""), 500


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

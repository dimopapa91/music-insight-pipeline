"""Taste-profile blueprint: /profile and /profile/refresh."""

import os
import json
from urllib.parse import quote

from flask import Blueprint, render_template, render_template_string, redirect

from db import db_cursor

taste_bp = Blueprint("taste", __name__)

_taste_cache = {}

_TASTE_ERROR = """<!DOCTYPE html><html><head><meta charset="utf-8">
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
<p>Could not load your taste profile. This might be a temporary database issue — try refreshing.</p>
<a href="/" class="back">← Back to dashboard</a>
</div></body></html>
"""


@taste_bp.route("/profile")
def taste_profile():
    import anthropic as _anthropic
    try:
        with db_cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (artist_name) artist_name, top_tracks
                FROM searches ORDER BY artist_name, searched_at DESC
            """)
            rows = cur.fetchall()
    except Exception:
        return render_template_string(_TASTE_ERROR), 500

    artists = [r[0] for r in rows]
    if not artists:
        return render_template("taste_profile.html", taste_analysis="No artists in your pipeline yet — search some first!", artists=[], artist_count=0, urlencode=quote)

    cache_key = ",".join(sorted(artists))
    if cache_key in _taste_cache:
        analysis = _taste_cache[cache_key]
    else:
        summaries = []
        for artist, top_raw in rows[:12]:
            tracks = top_raw if isinstance(top_raw, list) else json.loads(top_raw)
            track_names = ", ".join(t["name"] for t in tracks[:3])
            summaries.append(f"{artist} (top tracks: {track_names})")
        artist_block = "\n".join(f"- {s}" for s in summaries)
        prompt = f"""You are a music taste analyst. Here are the artists someone has been searching and their top tracks:

{artist_block}

Based on this, write a 2-3 paragraph taste profile in plain prose. Cover: what genres and sounds connect these artists, what this reveals about the listener's personality and taste, and what they might enjoy discovering next. No markdown, no bullet points, no headers — just clean conversational paragraphs."""
        try:
            _client = _anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            msg = _client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=600, messages=[{"role": "user", "content": prompt}])
            analysis = msg.content[0].text
            _taste_cache[cache_key] = analysis
        except Exception as e:
            analysis = f"Could not generate taste profile: {e}"

    return render_template("taste_profile.html", taste_analysis=analysis, artists=artists, artist_count=len(artists), urlencode=quote)


@taste_bp.route("/profile/refresh", methods=["POST"])
def taste_profile_refresh():
    _taste_cache.clear()
    return redirect("/profile")

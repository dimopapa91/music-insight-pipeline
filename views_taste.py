"""Taste-profile blueprint: /profile and /profile/refresh."""

import os
import json
from urllib.parse import quote

from flask import Blueprint, render_template, redirect

from db import db_cursor

taste_bp = Blueprint("taste", __name__)

_taste_cache = {}


@taste_bp.route("/profile")
def taste_profile():
    from flask_login import current_user

    # Logged-out: explain the feature; no personal data, no AI call.
    if not current_user.is_authenticated:
        return render_template("taste_profile.html", state="logged_out",
                               artists=[], artist_count=0, taste_analysis=None)

    import anthropic as _anthropic
    try:
        with db_cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (artist_name) artist_name, top_tracks
                FROM searches WHERE user_id = %s
                ORDER BY artist_name, searched_at DESC
            """, (current_user.id,))
            rows = cur.fetchall()
    except Exception:
        return render_template("error.html",
            heading="Something went wrong",
            message="Could not load your taste profile. This might be a temporary database issue. Try refreshing."), 500

    artists = [r[0] for r in rows]
    if not artists:
        return render_template("taste_profile.html", state="empty",
                               artists=[], artist_count=0, taste_analysis=None)

    cache_key = f"{current_user.id}:" + ",".join(sorted(artists))
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

Based on this, write a 2-3 paragraph taste profile in plain prose. Cover: what genres and sounds connect these artists, what this reveals about the listener's personality and taste, and what they might enjoy discovering next. No markdown, no bullet points, no headers, just clean conversational paragraphs. Do not use em dashes (the "—" character); use commas, colons or separate sentences instead. Do not infer sensitive personal characteristics."""
        try:
            _client = _anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            msg = _client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=600, messages=[{"role": "user", "content": prompt}])
            analysis = msg.content[0].text
            _taste_cache[cache_key] = analysis
        except Exception:
            # Never leak provider/exception detail to the user.
            analysis = None

    return render_template("taste_profile.html", state="ready",
                           taste_analysis=analysis, artists=artists,
                           artist_count=len(artists), urlencode=quote)


@taste_bp.route("/profile/refresh", methods=["POST"])
def taste_profile_refresh():
    _taste_cache.clear()
    return redirect("/profile")

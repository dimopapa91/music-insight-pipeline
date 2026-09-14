"""Taste-profile blueprint: /profile and /profile/refresh."""

import os
import json
import logging
from urllib.parse import quote

from flask import Blueprint, render_template, redirect
from flask_login import current_user, login_required

from db import db_cursor

taste_bp = Blueprint("taste", __name__)
logger = logging.getLogger(__name__)

_taste_cache = {}

# A written taste profile needs enough signal to say something real. Below
# this, the page shows honest "still forming" progress instead of guessing
# from one or two artists.
MIN_TASTE_ARTISTS = 3

# Existing cap on how many of the user's artists go into the prompt.
MAX_TASTE_ARTISTS = 12


@taste_bp.route("/profile")
def taste_profile():
    # Logged-out: explain the feature; no personal data, no AI call.
    if not current_user.is_authenticated:
        return render_template("taste_profile.html", state="logged_out",
                               artists=[], artist_count=0, taste_analysis=None,
                               min_taste_artists=MIN_TASTE_ARTISTS)

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

    # 0, 1, or 2 distinct artists: the wave is still forming. Never call the
    # provider for this — there isn't enough signal to say anything real yet.
    if len(artists) < MIN_TASTE_ARTISTS:
        return render_template("taste_profile.html", state="forming",
                               artists=artists, artist_count=len(artists), taste_analysis=None,
                               min_taste_artists=MIN_TASTE_ARTISTS)

    cache_key = f"{current_user.id}:" + ",".join(sorted(artists))
    if cache_key in _taste_cache:
        analysis = _taste_cache[cache_key]
    else:
        import anthropic as _anthropic

        summaries = []
        for artist, top_raw in rows[:MAX_TASTE_ARTISTS]:
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
        except Exception as e:
            # Never leak provider/exception detail to the user — but this
            # used to swallow the exception completely, which made a real
            # production failure undiagnosable. Log only safe, generic
            # metadata: never the prompt, artist list, track data, API key,
            # or generated text.
            status_code = getattr(e, "status_code", None)
            logger.error(
                "Taste generation failed: %s%s (artists=%d)",
                type(e).__name__,
                f" status_code={status_code}" if status_code is not None else "",
                len(artists),
            )
            analysis = None  # never cached — a fresh attempt is made next request

    return render_template("taste_profile.html", state="ready",
                           taste_analysis=analysis, artists=artists,
                           artist_count=len(artists), urlencode=quote,
                           min_taste_artists=MIN_TASTE_ARTISTS)


@taste_bp.route("/profile/refresh", methods=["POST"])
@login_required
def taste_profile_refresh():
    """Clear only the current user's cached Taste analyses, never anyone
    else's — cache keys are "<user_id>:<sorted artists>", so a plain
    prefix match scopes this correctly."""
    prefix = f"{current_user.id}:"
    for key in [k for k in _taste_cache if k.startswith(prefix)]:
        del _taste_cache[key]
    return redirect("/profile")

"""Taste-profile blueprint: /profile and /profile/refresh."""

import os
import json
import logging
from urllib.parse import quote

from flask import Blueprint, render_template, redirect, request, url_for
from flask_login import current_user, login_required

import lastfm_user
from db import db_cursor
from rate_limit import limiter
from text_clean import strip_em_dashes

taste_bp = Blueprint("taste", __name__)
logger = logging.getLogger(__name__)

_taste_cache = {}

# A written taste profile needs enough signal to say something real. Below
# this, the page shows honest "still forming" progress instead of guessing
# from one or two artists.
MIN_TASTE_ARTISTS = 3

# Existing cap on how many of the user's artists go into the prompt.
MAX_TASTE_ARTISTS = 12


def _listening_summary(user_id, username, period, taste):
    """Short AI read of real listening, cached per user/period/top artists.
    None on any provider failure (never cached, retried next request)."""
    top = taste["top"][:MAX_TASTE_ARTISTS]
    if len(top) < MIN_TASTE_ARTISTS:
        return None
    key = f"{user_id}:lastfm:{period}:" + ",".join(a["name"] for a in top)
    if key in _taste_cache:
        return _taste_cache[key]
    import anthropic as _anthropic
    artist_block = "\n".join(f"- {a['name']} ({a['plays']} plays)" for a in top)
    genre_block = ", ".join(g["name"] for g in taste["genres"]) or "unknown"
    prompt = f"""You are a music taste analyst. This is someone's real listening ({lastfm_user.PERIODS[period].lower()}), most played first:

{artist_block}

Their most-tagged sounds: {genre_block}.

Write a short taste read in 2 short paragraphs of plain prose (max 120 words total). Cover what connects these artists and what the balance of plays says about how they listen. No markdown, no lists, no headers. Do not use em dashes (the "\u2014" character). Do not infer sensitive personal characteristics."""
    try:
        client = _anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        msg = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=400,
                                     messages=[{"role": "user", "content": prompt}])
        text = strip_em_dashes(msg.content[0].text)
        _taste_cache[key] = text
        return text
    except Exception as e:
        status_code = getattr(e, "status_code", None)
        logger.error("Listening summary failed: %s%s", type(e).__name__,
                     f" status_code={status_code}" if status_code is not None else "")
        return None


@taste_bp.route("/profile")
def taste_profile():
    # Logged-out: explain the feature; no personal data, no AI call.
    if not current_user.is_authenticated:
        return render_template("taste_profile.html", state="logged_out",
                               artists=[], artist_count=0, taste_analysis=None,
                               min_taste_artists=MIN_TASTE_ARTISTS)

    # Connected Last.fm account: the profile comes from real listening.
    lastfm_name = lastfm_user.get_link(current_user.id)
    if lastfm_name:
        period = request.args.get("period", lastfm_user.DEFAULT_PERIOD)
        if period not in lastfm_user.PERIODS:
            period = lastfm_user.DEFAULT_PERIOD
        taste = lastfm_user.build_taste(lastfm_name, period)
        summary = _listening_summary(current_user.id, lastfm_name, period, taste) if taste["top"] else None
        top_plays = taste["top"][0]["plays"] if taste["top"] else 1
        return render_template("taste_profile.html", state="lastfm",
                               lastfm_name=lastfm_name, period=period,
                               periods=lastfm_user.PERIODS, taste=taste,
                               top_plays=top_plays or 1, taste_analysis=summary,
                               artists=[], artist_count=len(taste["top"]),
                               min_taste_artists=MIN_TASTE_ARTISTS, urlencode=quote)

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
        return render_template("taste_profile.html", state="forming", show_connect=True,
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
            # Belt and suspenders: the prompt already asks Claude not to use
            # em dashes, but that's a request, not a guarantee. Enforce it
            # deterministically before this is cached.
            analysis = strip_em_dashes(msg.content[0].text)
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

    return render_template("taste_profile.html", state="ready", show_connect=True,
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


@taste_bp.route("/profile/lastfm", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def connect_lastfm():
    name = lastfm_user.validate_username(request.form.get("lastfm_username", ""))
    if not name:
        return redirect(url_for("taste.taste_profile", lastfm_error=1))
    try:
        lastfm_user.set_link(current_user.id, name)
    except Exception:
        logger.exception("Could not save Last.fm link")
        return redirect(url_for("taste.taste_profile", lastfm_error=1))
    return redirect(url_for("taste.taste_profile"))


@taste_bp.route("/profile/lastfm/disconnect", methods=["POST"])
@login_required
def disconnect_lastfm():
    try:
        lastfm_user.remove_link(current_user.id)
    except Exception:
        logger.exception("Could not remove Last.fm link")
    return redirect(url_for("taste.taste_profile"))

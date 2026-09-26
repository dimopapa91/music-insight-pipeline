"""Main blueprint: dashboard, search, JSON APIs, Deezer preview, debug."""

import base64
from urllib.parse import quote

import requests as http_requests
from flask import (Blueprint, render_template, request, redirect, url_for, jsonify)
from flask_login import current_user

from db import db_cursor
from pipeline import run_pipeline
from rate_limit import limiter
from services import (
    get_dashboard_data, clear_dashboard_cache, artist_titlecase,
    SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET,
)
from social import get_feed

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def dashboard():
    message = request.args.get("message")
    error = request.args.get("error")
    total_searches, unique_artists, searches_today, artist_plays, latest_insights, discovery = get_dashboard_data()
    try:
        community_posts = get_feed(current_user.id if current_user.is_authenticated else None, scope="latest", page=1, per_page=3)
    except Exception:
        community_posts = []
    return render_template("index.html",
        total_searches=total_searches,
        unique_artists=unique_artists,
        searches_today=searches_today,
        artist_plays=artist_plays,
        latest_insights=latest_insights,
        discovery=discovery,
        community_posts=community_posts,
        message=message,
        error=error,
        urlencode=quote,
    )


@main_bp.route("/search", methods=["POST"])
@limiter.limit("10 per hour")
def search():
    artist = request.form.get("artist", "").strip()
    if not artist:
        return redirect(url_for("main.dashboard", message="Please enter an artist name.", error=True))
    uid = current_user.id if current_user.is_authenticated else None
    try:
        # The return value (the AI insight, or "" when only Claude failed)
        # isn't needed here: either way the search itself succeeded and the
        # artist page reads the saved row for itself.
        run_pipeline(artist, user_id=uid)
        # Saved by this point, so drop the cached homepage payload —
        # otherwise the artist the user just added wouldn't appear for up
        # to _DASHBOARD_TTL. Not on the failure path: nothing was saved.
        clear_dashboard_cache()
    except Exception:
        # Never leak the raw provider/DB exception text to a public message —
        # detailed diagnostics belong only in the pipeline's own logs.
        return redirect(url_for("main.dashboard",
            message=f"Could not load {artist_titlecase(artist)} just now. Check the spelling, or try again.",
            error=True))
    # The search succeeded either way here — with or without the optional
    # Claude enrichment — so go straight to what the user actually asked
    # for. Landing back on the dashboard meant hunting for the artist they
    # just searched; the artist page already states its own AI-unavailable
    # case, so no flash message is needed to carry that.
    return redirect(url_for("artist.artist_profile", artist_name=artist))


@main_bp.route("/about")
def about():
    return render_template("about.html")


@main_bp.route("/api/artists")
def api_artists():
    try:
        with db_cursor() as cur:
            cur.execute("SELECT DISTINCT artist_name FROM searches ORDER BY artist_name")
            artists = [row[0] for row in cur.fetchall()]
        return jsonify(artists)
    except Exception:
        return jsonify([])


@main_bp.route("/api/stats")
def api_stats():
    """Public stats for the portfolio site (dimospapageorgiou.com)."""
    try:
        with db_cursor() as cur:
            cur.execute("SELECT COUNT(*), COUNT(DISTINCT artist_name), MAX(searched_at) FROM searches")
            total, unique, last = cur.fetchone()
        resp = jsonify({
            "total_searches": total,
            "unique_artists": unique,
            "last_run": last.strftime("%d %b %Y") if last else None,
        })
    except Exception:
        resp = jsonify({})
    resp.headers["Access-Control-Allow-Origin"] = "https://dimospapageorgiou.com"
    return resp


@main_bp.route("/preview")
def preview():
    artist = request.args.get("artist", "")
    track = request.args.get("track", "")
    try:
        resp = http_requests.get(
            "https://api.deezer.com/search",
            params={"q": f"{artist} {track}", "limit": 1},
            timeout=5,
        )
        data = resp.json()
        if data.get("total", 0) > 0:
            r = data["data"][0]
            return {"preview_url": r.get("preview", ""), "title": r.get("title", track), "artist": r.get("artist", {}).get("name", artist)}
    except Exception:
        pass
    return {"preview_url": "", "title": track, "artist": artist}


@main_bp.route("/debug/spotify")
def debug_spotify():
    try:
        creds = base64.b64encode(f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()).decode()
        resp = http_requests.post("https://accounts.spotify.com/api/token",
            headers={"Authorization": f"Basic {creds}"},
            data={"grant_type": "client_credentials"}, timeout=5)
        data = resp.json()
        if resp.status_code == 200 and "access_token" in data:
            return jsonify({"success": True})
        return jsonify({
            "success": False,
            "status_code": resp.status_code,
            "error": data.get("error"),
            "error_description": data.get("error_description"),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@main_bp.route("/debug/sentry")
def debug_sentry():
    """Deliberately raise so we can confirm errors reach Sentry after a
    deploy. Auth-gated so a crawler can't spam Sentry with 500s."""
    if not current_user.is_authenticated:
        return ("Not found", 404)
    raise RuntimeError("Sentry test error from /debug/sentry — safe to ignore.")

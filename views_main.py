"""Main blueprint: dashboard, search, JSON APIs, Deezer preview, debug."""

import base64
from urllib.parse import quote

import requests as http_requests
from flask import (Blueprint, render_template, request, redirect, url_for, jsonify)
from flask_login import current_user

from db import db_cursor
from pipeline import run_pipeline
from services import (
    get_dashboard_data, artist_titlecase,
    SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET,
)

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def dashboard():
    message = request.args.get("message")
    error = request.args.get("error")
    total_searches, unique_artists, searches_today, artist_plays, latest_insights, discovery = get_dashboard_data()
    return render_template("index.html",
        total_searches=total_searches,
        unique_artists=unique_artists,
        searches_today=searches_today,
        artist_plays=artist_plays,
        latest_insights=latest_insights,
        discovery=discovery,
        message=message,
        error=error,
        urlencode=quote,
    )


@main_bp.route("/search", methods=["POST"])
def search():
    artist = request.form.get("artist", "").strip()
    if not artist:
        return redirect(url_for("main.dashboard", message="Please enter an artist name.", error=True))
    try:
        uid = current_user.id if current_user.is_authenticated else None
        run_pipeline(artist, user_id=uid)
        return redirect(url_for("main.dashboard", message=f"✅ {artist_titlecase(artist)} analysed and saved successfully!"))
    except Exception as e:
        return redirect(url_for("main.dashboard", message=f"❌ Could not analyse {artist_titlecase(artist)}: {str(e)}", error=True))


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

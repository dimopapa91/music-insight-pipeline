"""Shared services and helpers for Waveline.

Data-source clients (Spotify, Last.fm, Deezer, RSS), the dashboard/compare data
builders, and the Jinja display filters all live here so the view blueprints
stay thin. No Flask app or routes in this module.
"""

import os
import json
import re
import html
import time
import base64
import logging
import xml.etree.ElementTree as ET

import requests as http_requests
import markdown as markdown_lib
from markupsafe import Markup

from db import db_cursor

logger = logging.getLogger(__name__)

LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")
LASTFM_BASE = "http://ws.audioscrobbler.com/2.0/"

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
_spotify_token_cache = {}


# ── Jinja display filters ───────────────────────────────────────────

def render_markdown(text):
    """Render AI-generated Markdown text into safe HTML."""
    if not text:
        return Markup("")
    escaped = html.escape(text)
    return Markup(markdown_lib.markdown(escaped, extensions=["nl2br"]))


def markdown_preview(text, length=200):
    """Plain-text teaser derived from rendered Markdown, for truncated previews."""
    if not text:
        return ""
    rendered = markdown_lib.markdown(text)
    plain = re.sub(r"<[^>]+>", "", rendered)
    plain = html.unescape(" ".join(plain.split()))
    return plain[:length]


def artist_titlecase(name):
    """Capitalise the first letter of each word without lowercasing the rest,
    preserving stylised capitalisation like 'BlakeNor' or 'MGMT'."""
    if not name:
        return name
    return " ".join(w[:1].upper() + w[1:] for w in name.split(" "))


# ── Spotify ─────────────────────────────────────────────────────────

def get_spotify_token():
    cached = _spotify_token_cache
    if cached.get("token") and cached.get("expires_at", 0) > time.time():
        return cached["token"]
    try:
        creds = base64.b64encode(f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()).decode()
        resp = http_requests.post("https://accounts.spotify.com/api/token",
            headers={"Authorization": f"Basic {creds}"},
            data={"grant_type": "client_credentials"}, timeout=5)
        data = resp.json()
        if "access_token" in data:
            cached["token"] = data["access_token"]
            cached["expires_at"] = time.time() + data.get("expires_in", 3600) - 60
            return cached["token"]
    except Exception:
        pass
    return None


def get_spotify_artist(artist_name):
    """Returns dict with genres, popularity, followers — or empty dict on failure."""
    token = get_spotify_token()
    if not token:
        return {}
    try:
        resp = http_requests.get("https://api.spotify.com/v1/search",
            headers={"Authorization": f"Bearer {token}"},
            params={"q": artist_name, "type": "artist", "limit": 1}, timeout=5)
        data = resp.json()
        items = data.get("artists", {}).get("items", [])
        if not items:
            return {}
        a = items[0]
        return {
            "popularity": a.get("popularity", 0),
            "followers": a.get("followers", {}).get("total", 0),
            "genres": a.get("genres", [])[:4],
            "spotify_url": a.get("external_urls", {}).get("spotify", ""),
            "image": a["images"][1]["url"] if len(a.get("images", [])) > 1 else (a["images"][0]["url"] if a.get("images") else ""),
        }
    except Exception:
        return {}


# ── Last.fm / Deezer discovery ──────────────────────────────────────

def get_similar_artists(artist_name):
    try:
        resp = http_requests.get(LASTFM_BASE, params={
            "method": "artist.getSimilar",
            "artist": artist_name,
            "api_key": LASTFM_API_KEY,
            "format": "json",
            "limit": 5
        }, timeout=5)
        data = resp.json()
        if "similarartists" in data and "artist" in data["similarartists"]:
            return [a["name"] for a in data["similarartists"]["artist"][:5]]
    except Exception:
        pass
    return []


def get_discovery_artists(searched_artists):
    similar = []
    seen = set()
    for artist in searched_artists[:4]:
        for s in get_similar_artists(artist):
            if s not in seen:
                seen.add(s)
                similar.append(s)

    already = set(a.lower() for a in searched_artists)
    new_artists = [a for a in similar if a.lower() not in already][:8]

    discovery = []
    for artist in new_artists:
        try:
            resp = http_requests.get(
                "https://api.deezer.com/search/artist",
                params={"q": artist, "limit": 1},
                timeout=4
            )
            data = resp.json()
            if data.get("total", 0) > 0:
                d = data["data"][0]
                discovery.append({
                    "name": d.get("name", artist),
                    "image": d.get("picture_medium", ""),
                    "nb_fan": d.get("nb_fan", 0)
                })
            else:
                discovery.append({"name": artist, "image": "", "nb_fan": 0})
        except Exception:
            discovery.append({"name": artist, "image": "", "nb_fan": 0})
    return discovery


# ── Dashboard + compare data builders ───────────────────────────────

def get_dashboard_data():
    with db_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM searches")
        total_searches = cur.fetchone()[0]

        cur.execute("SELECT COUNT(DISTINCT artist_name) FROM searches")
        unique_artists = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM searches WHERE DATE(searched_at) = CURRENT_DATE")
        searches_today = cur.fetchone()[0]

        cur.execute("SELECT artist_name, top_tracks FROM searches")
        rows = cur.fetchall()
        artist_avg = {}
        for artist_name, top_tracks in rows:
            tracks = top_tracks if isinstance(top_tracks, list) else json.loads(top_tracks)
            plays = [int(t["playcount"]) for t in tracks]
            avg = sum(plays) // len(plays)
            if artist_name not in artist_avg:
                artist_avg[artist_name] = []
            artist_avg[artist_name].append(avg)
        final_avgs = {k: sum(v) // len(v) for k, v in artist_avg.items()}
        sorted_avgs = sorted(final_avgs.items(), key=lambda x: x[1], reverse=True)
        max_avg = sorted_avgs[0][1] if sorted_avgs else 1
        artist_plays = [(a, avg, max_avg) for a, avg in sorted_avgs]

        cur.execute("""
            SELECT * FROM (
                SELECT DISTINCT ON (artist_name) artist_name, claude_insight, searched_at, top_tracks
                FROM searches
                ORDER BY artist_name, searched_at DESC
            ) sub
            ORDER BY searched_at DESC LIMIT 5
        """)

        class Row:
            def __init__(self, r):
                self.artist = r[0]
                self.insight = r[1]
                self.searched_at = r[2]
                tracks_raw = r[3] if isinstance(r[3], list) else json.loads(r[3])
                self.top_tracks = [t["name"] for t in tracks_raw[:4]]
                self.similar_artists = get_similar_artists(r[0])

        latest_insights = [Row(r) for r in cur.fetchall()]

        cur.execute("SELECT DISTINCT artist_name FROM searches ORDER BY artist_name")
        all_artists = [r[0] for r in cur.fetchall()]

    # DB connection released before the (slower) external discovery calls
    discovery = get_discovery_artists(all_artists)

    return total_searches, unique_artists, searches_today, artist_plays, latest_insights, discovery


def get_artist_db(name):
    try:
        with db_cursor() as cur:
            cur.execute("""
                SELECT artist_name, claude_insight, top_tracks FROM searches
                WHERE LOWER(artist_name) = LOWER(%s)
                ORDER BY searched_at DESC LIMIT 1
            """, (name,))
            row = cur.fetchone()
        if not row:
            return None
        tracks_raw = row[2] if isinstance(row[2], list) else json.loads(row[2])
        spotify = get_spotify_artist(row[0])
        return {
            "name": row[0],
            "insight": row[1],
            "tracks": [t["name"] for t in tracks_raw[:5]],
            "spotify_url": spotify.get("spotify_url", ""),
            "image": spotify.get("image", "")
        }
    except Exception:
        return None


# ── Music news (RSS + Spotify new releases) ─────────────────────────

_news_cache = {"data": None, "fetched_at": 0}

RSS_FEEDS = [
    {"name": "Pitchfork",        "url": "https://pitchfork.com/rss/news/feed.xml",          "color": "#e00"},
    {"name": "NME",              "url": "https://www.nme.com/feed",                          "color": "#ff6900"},
    {"name": "The Guardian",     "url": "https://www.theguardian.com/music/rss",             "color": "#005689"},
    {"name": "Resident Advisor", "url": "https://ra.co/xml/news.xml",                        "color": "#1da0c3"},
]


def fetch_rss(feed):
    try:
        resp = http_requests.get(feed["url"], timeout=6, headers={"User-Agent": "MusicInsightPipeline/1.0"})
        root = ET.fromstring(resp.content)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items = []
        channel = root.find("channel")
        entries = (channel.findall("item") if channel is not None else []) or root.findall("atom:entry", ns)
        for entry in entries[:4]:
            title = (entry.findtext("title") or entry.findtext("atom:title", namespaces=ns) or "").strip()
            link  = (entry.findtext("link")  or entry.findtext("atom:link[@rel='alternate']", namespaces=ns) or "")
            if not link:
                link_el = entry.find("atom:link", ns)
                link = link_el.get("href", "") if link_el is not None else ""
            pub   = (entry.findtext("pubDate") or entry.findtext("atom:published", namespaces=ns) or "")
            if title and link:
                items.append({"title": title.replace("&amp;", "&"), "link": link.strip(), "pub": pub[:16], "source": feed["name"], "color": feed["color"]})
        return items
    except Exception:
        return []


def get_spotify_new_releases(limit=12):
    token = get_spotify_token()
    if not token:
        return []
    try:
        resp = http_requests.get("https://api.spotify.com/v1/browse/new-releases",
            headers={"Authorization": f"Bearer {token}"},
            params={"limit": limit, "country": "GB"}, timeout=5)
        albums = resp.json().get("albums", {}).get("items", [])
        results = []
        for a in albums:
            results.append({
                "name":    a.get("name", ""),
                "artist":  ", ".join(ar["name"] for ar in a.get("artists", [])[:2]),
                "image":   a["images"][1]["url"] if len(a.get("images", [])) > 1 else (a["images"][0]["url"] if a.get("images") else ""),
                "url":     a.get("external_urls", {}).get("spotify", ""),
                "type":    a.get("album_type", "album").capitalize(),
                "date":    a.get("release_date", ""),
            })
        return results
    except Exception:
        return []


def get_news_data():
    if _news_cache["data"] and time.time() - _news_cache["fetched_at"] < 3600:
        return _news_cache["data"]
    articles = []
    for feed in RSS_FEEDS:
        articles.extend(fetch_rss(feed))
    releases = get_spotify_new_releases()
    _news_cache["data"] = {"articles": articles, "releases": releases}
    _news_cache["fetched_at"] = time.time()
    return _news_cache["data"]


def clear_news_cache():
    _news_cache["data"] = None
    _news_cache["fetched_at"] = 0

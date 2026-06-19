from flask import Flask, render_template_string, request, redirect, url_for
import psycopg2
import os
import json
import requests as http_requests
from dotenv import load_dotenv
from pipeline import run_pipeline

load_dotenv()

app = Flask(__name__)

LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")
LASTFM_BASE = "http://ws.audioscrobbler.com/2.0/"

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
_spotify_token_cache = {}

def get_spotify_token():
    import time, base64
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
    # Collect similar artists across top 4 searched
    similar = []
    seen = set()
    for artist in searched_artists[:4]:
        for s in get_similar_artists(artist):
            if s not in seen:
                seen.add(s)
                similar.append(s)

    # Filter out already searched
    already = set(a.lower() for a in searched_artists)
    new_artists = [a for a in similar if a.lower() not in already][:8]

    # Fetch images + fan count from Deezer
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

def get_db_connection():
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        # Railway provides DATABASE_URL — use it directly
        return psycopg2.connect(database_url)
    # Local development fallback
    return psycopg2.connect(dbname="music_insights", user=os.getenv("USER"))

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Waveline</title>
    <link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: 'Inter', sans-serif;
            background-color: #e2e2df;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='1440' height='320' viewBox='0 0 1440 320'%3E%3Cpath fill='none' stroke='%231da0c3' stroke-width='1.2' stroke-opacity='0.10' d='M0,160 C60,130 120,190 180,160 C240,130 300,100 360,130 C420,160 480,200 540,170 C600,140 660,110 720,140 C780,170 840,210 900,180 C960,150 1020,100 1080,130 C1140,160 1200,200 1260,170 C1320,140 1380,130 1440,150'/%3E%3Cpath fill='none' stroke='%231da0c3' stroke-width='1' stroke-opacity='0.07' d='M0,200 C80,170 160,230 240,200 C320,170 400,130 480,160 C560,190 640,220 720,190 C800,160 880,120 960,150 C1040,180 1120,210 1200,185 C1280,160 1360,150 1440,170'/%3E%3Cpath fill='none' stroke='%231da0c3' stroke-width='0.8' stroke-opacity='0.06' d='M0,120 C100,100 200,150 300,120 C400,90 500,70 600,100 C700,130 800,160 900,130 C1000,100 1100,80 1200,110 C1300,140 1380,130 1440,120'/%3E%3C/svg%3E");
            background-repeat: no-repeat;
            background-position: center 60%;
            background-size: 120% auto;
            color: #111;
            min-height: 100vh;
            transition: background 0.2s, color 0.2s;
        }


        /* ── DARK MODE ── */
        body.dark { background-color: #0d0d0d; color: #e0e0e0; }
        body.dark .hero { background: #111; border-color: #222; }
        body.dark .hero h1 { color: #fff; }
        body.dark .hero-sub { color: #555; }
        body.dark .search-wrap { background: #111; border-color: #222; }
        body.dark .search-form input { background: #1a1a1a; border-color: #333; color: #e0e0e0; }
        body.dark .search-form input::placeholder { color: #444; }
        body.dark .suggestions { background: #1a1a1a; border-color: #333; }
        body.dark .suggestion { color: #e0e0e0; }
        body.dark .suggestion:hover { background: #222; }
        body.dark .alert { background: #1a1600; border-color: #333; }
        body.dark .panel { background: #111; border-color: #222; }
        body.dark .panel-title { color: #e0e0e0; border-color: #333; }
        body.dark .bar-name { color: #888; }
        body.dark .bar-track { background: #222; }
        body.dark .insight-card { border-color: #1e1e1e; }
        body.dark .insight-artist { color: #e0e0e0; }
        body.dark .insight-time { color: #444; }
        body.dark .insight-body { color: #777; }
        body.dark .track-tag { background: #1a1a1a; border-color: #2a2a2a; color: #888; }
        body.dark .similar-chip { background: #1a1a1a; border-color: #2a2a2a; color: #e0e0e0; }
        body.dark .discovery-section { background: #111; border-color: #222; }
        body.dark .discovery-header { color: #e0e0e0; border-color: #444; }
        body.dark .discovery-name { color: #e0e0e0; }
        body.dark .discovery-img { border-color: #222; background: #1a1a1a; }
        body.dark .footer { background: #000; }

        /* ── TICKER ── */
        .ticker {
            background: #1da0c3;
            color: #fff;
            font-family: 'Space Mono', monospace;
            font-size: 0.65em;
            letter-spacing: 2px;
            text-transform: uppercase;
            padding: 6px 0;
            overflow: hidden;
            white-space: nowrap;
        }
        .ticker-inner {
            display: inline-block;
            animation: ticker 28s linear infinite;
        }
        .ticker-inner span { margin-right: 60px; }
        @keyframes ticker {
            0%   { transform: translateX(0); }
            100% { transform: translateX(-50%); }
        }

        /* ── NTS-STYLE TOPBAR ── */
        .topbar {
            background: #000;
            padding: 0 30px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            height: 48px;
        }
        .topbar-brand {
            font-family: 'Space Mono', monospace;
            font-size: 0.85em;
            font-weight: 700;
            letter-spacing: 2px;
            text-transform: uppercase;
            color: #fff;
        }
        .topbar-right {
            display: flex;
            align-items: center;
            gap: 20px;
        }
        .topbar-link {
            font-family: 'Space Mono', monospace;
            font-size: 0.62em;
            letter-spacing: 2px;
            text-transform: uppercase;
            color: #1da0c3;
        }
        .live-pill {
            display: flex;
            align-items: center;
            gap: 6px;
            background: #1a1a1a;
            border: 1px solid #333;
            border-radius: 3px;
            padding: 4px 10px;
            font-family: 'Space Mono', monospace;
            font-size: 0.6em;
            letter-spacing: 2px;
            text-transform: uppercase;
            color: #fff;
        }
        .dot {
            width: 6px; height: 6px;
            background: #1da0c3;
            border-radius: 50%;
            animation: blink 2s infinite;
        }
        @keyframes blink {
            0%, 100% { opacity: 1; }
            50%       { opacity: 0.2; }
        }

        /* ── HERO / HEADER ── */
        .hero {
            background: #fff;
            border-bottom: 2px solid #111;
            padding: 48px 30px 36px;
            text-align: center;
        }
        .hero-eyebrow {
            font-family: 'Space Mono', monospace;
            font-size: 0.65em;
            letter-spacing: 4px;
            text-transform: uppercase;
            color: #1da0c3;
            margin-bottom: 14px;
        }
        .hero h1 {
            font-family: 'Space Mono', monospace;
            font-size: 3em;
            font-weight: 700;
            letter-spacing: -2px;
            line-height: 1;
            text-transform: uppercase;
            color: #111;
        }
        .hero-sub {
            font-size: 0.78em;
            color: #999;
            margin-top: 14px;
            letter-spacing: 1px;
        }

        /* ── SEARCH ── */
        .search-wrap {
            background: #fff;
            border-bottom: 1px solid #ddd;
            padding: 22px 30px;
            display: flex;
            justify-content: center;
            position: relative;
            z-index: 50;
        }
        .search-form-wrapper {
            position: relative;
            width: 100%;
            max-width: 440px;
            z-index: 50;
        }
        .search-form {
            display: flex;
            width: 100%;
        }
        .suggestions {
            position: absolute;
            top: 100%;
            left: 0;
            right: 60px;
            background: #fff;
            border: 1px solid #1da0c3;
            border-top: none;
            z-index: 999;
            display: none;
            box-shadow: 0 8px 24px rgba(0,0,0,0.12);
        }
        .suggestion {
            padding: 9px 20px;
            font-family: 'Space Mono', monospace;
            font-size: 0.75em;
            cursor: pointer;
            color: #333;
            border-bottom: 1px solid #f0f0f0;
        }
        .suggestion:hover { background: #e2e2df; }
        .search-form input {
            flex: 1;
            padding: 11px 20px;
            background: #e2e2df;
            border: 1px solid #ddd;
            border-right: none;
            border-radius: 3px 0 0 3px;
            font-family: 'Space Mono', monospace;
            font-size: 0.78em;
            color: #111;
            outline: none;
            transition: border-color 0.15s;
        }
        .search-form input::placeholder { color: #bbb; }
        .search-form input:focus { border-color: #1da0c3; }
        .search-form button {
            padding: 11px 22px;
            background: #111;
            color: #fff;
            border: none;
            border-radius: 0 3px 3px 0;
            font-family: 'Space Mono', monospace;
            font-size: 0.72em;
            font-weight: 700;
            letter-spacing: 2px;
            text-transform: uppercase;
            cursor: pointer;
            transition: background 0.15s;
        }
        .search-form button:hover { background: #1da0c3; }

        .alert {
            padding: 11px 30px;
            background: #fffbf0;
            border-bottom: 1px solid #f0e68c;
            font-family: 'Space Mono', monospace;
            font-size: 0.72em;
            color: #7a6000;
        }
        .alert.error { background: #fff5f5; border-bottom-color: #fcc; color: #c0392b; }

        /* ── STATS ── */
        .stats-strip {
            display: flex;
            background: #111;
            border-bottom: 2px solid #1da0c3;
        }
        .stat {
            flex: 1;
            padding: 20px 30px;
            border-right: 1px solid #222;
        }
        .stat:last-child { border-right: none; }
        .stat-number {
            font-family: 'Space Mono', monospace;
            font-size: 1.9em;
            font-weight: 700;
            color: #fff;
            line-height: 1;
        }
        .stat-label {
            font-size: 0.72em;
            color: #1da0c3;
            letter-spacing: 3px;
            text-transform: uppercase;
            margin-top: 6px;
            font-family: 'Space Mono', monospace;
        }

        /* keep content above body::before symbols */
        .topbar, .stats-strip, .alert, .main-grid, .discovery-section, .ticker-wrap {
            position: relative;
            z-index: 1;
        }

        /* ── TWO-COLUMN GRID ── */
        .main-grid {
            display: grid;
            grid-template-columns: 280px 1fr;
        }

        /* ── PANELS ── */
        .panel {
            padding: 30px;
            background: #fff;
            border-right: 1px solid #e8e8e8;
        }
        .panel:last-child { border-right: none; }
        .panel-title {
            font-family: 'Space Mono', monospace;
            font-size: 0.62em;
            letter-spacing: 4px;
            text-transform: uppercase;
            color: #111;
            margin-bottom: 22px;
            padding-bottom: 10px;
            border-bottom: 2px solid #111;
        }

        /* ── BARS ── */
        .bar-row {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 14px;
        }
        .bar-name {
            font-family: 'Space Mono', monospace;
            font-size: 0.72em;
            color: #444;
            width: 140px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .bar-track {
            flex: 1;
            height: 3px;
            background: #eee;
        }
        .bar-fill {
            height: 100%;
            background: #1da0c3;
        }
        .bar-val {
            font-family: 'Space Mono', monospace;
            font-size: 0.66em;
            color: #bbb;
            width: 100px;
            text-align: right;
        }

        /* ── INSIGHT CARDS ── */
        .insight-card {
            padding: 18px 0;
            border-bottom: 1px solid #f0f0f0;
        }
        .insight-card:last-child { border-bottom: none; }
        .insight-meta {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 6px;
        }
        .insight-artist {
            font-family: 'Space Mono', monospace;
            font-size: 0.82em;
            font-weight: 700;
            color: #111;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        .insight-time {
            font-family: 'Space Mono', monospace;
            font-size: 0.6em;
            color: #bbb;
            letter-spacing: 1px;
        }
        .track-tags {
            display: flex;
            flex-wrap: wrap;
            gap: 5px;
            margin-bottom: 10px;
        }
        .track-tag {
            font-family: 'Space Mono', monospace;
            font-size: 0.6em;
            color: #555;
            background: #f0f0f0;
            border: 1px solid #e0e0e0;
            border-radius: 2px;
            padding: 2px 8px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            max-width: 180px;
            text-decoration: none;
            cursor: pointer;
            transition: background 0.15s, color 0.15s;
        }
        .track-tag:hover {
            background: #111;
            color: #fff;
            border-color: #111;
        }
        .insight-body {
            font-size: 0.8em;
            color: #555;
            line-height: 1.75;
        }
        .insight-preview { display: block; }
        .insight-full    { display: none; }
        .insight-toggle {
            margin-top: 8px;
            display: inline-block;
            font-family: 'Space Mono', monospace;
            font-size: 0.65em;
            letter-spacing: 1px;
            color: #1da0c3;
            cursor: pointer;
            background: none;
            border: none;
            padding: 0;
            text-decoration: underline;
            text-underline-offset: 3px;
        }
        .insight-toggle:hover { color: #111; }

        /* ── SIMILAR ARTISTS ── */
        .similar-section {
            margin-top: 10px;
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 6px;
        }
        .similar-label {
            font-family: 'Space Mono', monospace;
            font-size: 0.6em;
            letter-spacing: 2px;
            text-transform: uppercase;
            color: #bbb;
            margin-right: 4px;
        }
        .similar-chip {
            font-family: 'Space Mono', monospace;
            font-size: 0.62em;
            color: #111;
            background: #f0f0f0;
            border: 1px solid #ddd;
            border-radius: 2px;
            padding: 3px 10px;
            cursor: pointer;
            transition: background 0.15s, border-color 0.15s;
        }
        .similar-chip:hover {
            background: #1da0c3;
            border-color: #1da0c3;
            color: #fff;
        }

        /* ── DISCOVERY SECTION ── */
        .discovery-section {
            background: #fff;
            border-top: 1px solid #e8e8e8;
            padding: 30px;
        }
        .discovery-header {
            font-family: 'Space Mono', monospace;
            font-size: 0.62em;
            letter-spacing: 4px;
            text-transform: uppercase;
            color: #111;
            margin-bottom: 22px;
            padding-bottom: 10px;
            border-bottom: 2px solid #111;
        }
        .discovery-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
            gap: 20px;
        }
        .discovery-card {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .discovery-img {
            width: 100%;
            aspect-ratio: 1;
            object-fit: cover;
            border: 1px solid #eee;
            background: #e2e2df;
        }
        .discovery-name {
            font-family: 'Space Mono', monospace;
            font-size: 0.7em;
            font-weight: 700;
            color: #111;
            line-height: 1.3;
        }
        .discovery-fans {
            font-family: 'Space Mono', monospace;
            font-size: 0.58em;
            color: #bbb;
        }
        .discovery-btn {
            margin-top: 2px;
            padding: 5px 0;
            background: #111;
            color: #fff;
            border: none;
            border-radius: 2px;
            font-family: 'Space Mono', monospace;
            font-size: 0.6em;
            letter-spacing: 1px;
            text-transform: uppercase;
            cursor: pointer;
            transition: background 0.15s;
            width: 100%;
        }
        .discovery-btn:hover { background: #1da0c3; }

        /* ── MINI PLAYER ── */
        .mini-player {
            position: fixed;
            bottom: 0; left: 0; right: 0;
            background: #111;
            display: none;
            align-items: center;
            justify-content: space-between;
            padding: 12px 30px;
            z-index: 1000;
            border-top: 2px solid #1da0c3;
            gap: 20px;
        }
        .player-info {
            display: flex;
            flex-direction: column;
            min-width: 160px;
        }
        .player-title {
            font-family: 'Space Mono', monospace;
            font-size: 0.75em;
            color: #fff;
            font-weight: 700;
        }
        .player-artist {
            font-family: 'Space Mono', monospace;
            font-size: 0.62em;
            color: #1da0c3;
            margin-top: 2px;
        }
        .player-note {
            font-family: 'Space Mono', monospace;
            font-size: 0.55em;
            color: #555;
            margin-top: 2px;
        }
        #audio-player {
            flex: 1;
            height: 32px;
            accent-color: #1da0c3;
        }
        .player-close {
            background: none;
            border: none;
            color: #555;
            font-size: 1em;
            cursor: pointer;
            padding: 4px 8px;
        }
        .player-close:hover { color: #fff; }

        /* ── FOOTER ── */
        .footer {
            background: #000;
            padding: 20px 30px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .footer-brand {
            font-family: 'Space Mono', monospace;
            font-size: 0.7em;
            font-weight: 700;
            letter-spacing: 2px;
            text-transform: uppercase;
            color: #fff;
        }
        .footer-sub {
            font-family: 'Space Mono', monospace;
            font-size: 0.6em;
            letter-spacing: 2px;
            text-transform: uppercase;
            color: #444;
        }

        /* ── MOBILE ── */
        @media (max-width: 700px) {
            .topbar { padding: 0 14px; }
            .topbar-brand { font-size: 0.85em; letter-spacing: 1px; }
            .topbar-right { gap: 10px; }
            .topbar-link { font-size: 0.55em; letter-spacing: 1px; }
            .live-pill { display: none; }
            .ticker { display: none; }
            .hero { padding: 24px 16px 20px; }
            .hero h1 { font-size: 2em; }
            .hero-eyebrow { font-size: 0.6em; }
            .hero-sub { font-size: 0.75em; }
            .search-wrap { padding: 14px 16px; }
            .search-form-wrapper { max-width: 100%; }
            .stats-strip { gap: 0; }
            .stat { padding: 14px 12px; }
            .stat-num { font-size: 1.6em; }
            .stat-label { font-size: 0.6em; }
            .main-grid { grid-template-columns: 1fr; }
            .panel { border-right: none; border-bottom: 1px solid #e8e8e8; }
            .bar-row { gap: 6px; }
            .bar-name { font-size: 0.62em; min-width: 80px; }
            .insight-card { padding: 14px 16px; }
            .insight-artist { font-size: 0.78em; }
            .track-tags { gap: 6px; }
            .track-tag { font-size: 0.65em; padding: 4px 8px; }
            .similar-chips { gap: 6px; }
            .similar-chip { font-size: 0.6em; }
            .discovery-section { padding: 20px 16px; }
            .discovery-grid { grid-template-columns: repeat(2, 1fr); gap: 12px; }
            .footer { flex-direction: column; gap: 8px; text-align: center; padding: 16px; }
            #mini-player { flex-wrap: wrap; padding: 8px 12px; gap: 8px; }
            #mini-player audio { width: 100%; }
        }
    </style>
</head>
<body>

    <!-- TICKER -->
    <div class="ticker">
        <div class="ticker-inner">
            <span>Waveline — Live</span>
            <span>Last.fm · Spotify · Deezer</span>
            <span>Manchester, UK — 2026</span>
            <span>Real-time Music Discovery</span>
            <span>Waveline — Live</span>
            <span>Last.fm · Spotify · Deezer</span>
            <span>Manchester, UK — 2026</span>
            <span>Real-time Music Discovery</span>
        </div>
    </div>

    <!-- TOPBAR -->
    <div class="topbar">
        <span class="topbar-brand">WAVELINE</span>
        <div class="topbar-right">
            <a href="/news" class="topbar-link" style="text-decoration:none">News</a>
            <a href="/compare" class="topbar-link" style="text-decoration:none">Compare</a>
            <a href="/profile" class="topbar-link" style="text-decoration:none">Taste Profile</a>
            <span class="live-pill"><span class="dot"></span>Live</span>
            <button id="dark-btn" onclick="toggleDark()" style="background:none;border:1px solid #333;color:#888;font-size:0.85em;padding:3px 8px;cursor:pointer;border-radius:3px;">◑</button>
        </div>
    </div>

    <!-- HERO -->
    <div class="hero">
        <div class="hero-eyebrow">Music Discovery · Manchester</div>
        <h1>Waveline</h1>
        <p class="hero-sub">Search any artist — get instant insights into their top tracks</p>
    </div>

    <!-- SEARCH -->
    <form method="POST" action="/search" id="search-form" autocomplete="off">
        <div class="search-wrap">
            <div class="search-form-wrapper">
                <div class="search-form">
                    <input type="text" name="artist" id="artist-input" placeholder="Search an artist… (press / to focus)" required />
                    <button type="submit">Analyse</button>
                </div>
                <div id="suggestions" class="suggestions"></div>
            </div>
        </div>
    </form>

    {% if message %}
    <div class="alert {{ 'error' if error else '' }}">{{ message }}</div>
    {% endif %}

    <!-- STATS -->
    <div class="stats-strip">
        <div class="stat">
            <div class="stat-number">{{ total_searches }}</div>
            <div class="stat-label">Total Searches</div>
        </div>
        <div class="stat">
            <div class="stat-number">{{ unique_artists }}</div>
            <div class="stat-label">Unique Artists</div>
        </div>
        <div class="stat">
            <div class="stat-number">{{ searches_today }}</div>
            <div class="stat-label">Today</div>
        </div>
    </div>

    <!-- MAIN GRID -->
    <div class="main-grid">

        <!-- LEFT: PLAYS CHART -->
        <div class="panel">
            <div class="panel-title">Average Plays by Artist</div>
            {% for artist, avg, max_avg in artist_plays %}
            <div class="bar-row">
                <span class="bar-name">{{ artist }}</span>
                <div class="bar-track">
                    <div class="bar-fill" style="width: {{ [((avg / max_avg) * 100)|int, 1]|max }}%"></div>
                </div>
                <span class="bar-val">{{ "{:,}".format(avg) }}</span>
            </div>
            {% endfor %}
        </div>

        <!-- RIGHT: INSIGHTS -->
        <div class="panel">
            <div class="panel-title">Latest Insights</div>
            {% for row in latest_insights %}
            {% set clean = row.insight | replace('##', '') | replace('**', '') | replace('# ', '') %}
            <div class="insight-card">
                <div class="insight-meta">
                    <a href="/artist/{{ row.artist | urlencode }}" class="insight-artist" style="text-decoration:none;color:inherit;">{{ row.artist }}</a>
                    <span class="insight-time">{{ row.searched_at.strftime('%d %b %Y  %H:%M') }}</span>
                </div>
                <div class="track-tags">
                    {% for t in row.top_tracks %}
                    <a class="track-tag" href="#" onclick="playTrack('{{ row.artist }}', '{{ t }}'); return false;">▶ {{ t }}</a>
                    {% endfor %}
                </div>
                <div class="insight-body" id="body-{{ loop.index }}">
                    <span class="insight-preview">{{ clean[:200] }}...</span>
                    <span class="insight-full">{{ clean }}</span>
                </div>
                <button class="insight-toggle" onclick="toggleInsight({{ loop.index }}, this)">Read more</button>
                {% if row.similar_artists %}
                <div class="similar-section">
                    <span class="similar-label">Similar</span>
                    {% for s in row.similar_artists %}
                    <form method="POST" action="/search" style="display:inline;margin:0">
                        <input type="hidden" name="artist" value="{{ s }}">
                        <button type="submit" class="similar-chip">{{ s }}</button>
                    </form>
                    {% endfor %}
                </div>
                {% endif %}
            </div>
            {% endfor %}
        </div>

    </div>

    <!-- DISCOVERY -->
    {% if discovery %}
    <div class="discovery-section">
        <div class="discovery-header">Discover · Similar Artists You Haven't Explored Yet</div>
        <div class="discovery-grid">
            {% for d in discovery %}
            <div class="discovery-card">
                {% if d.image %}
                <img src="{{ d.image }}" alt="{{ d.name }}" class="discovery-img">
                {% else %}
                <div class="discovery-img"></div>
                {% endif %}
                <div class="discovery-name">{{ d.name }}</div>
                {% if d.nb_fan %}
                <div class="discovery-fans">{{ "{:,}".format(d.nb_fan) }} fans</div>
                {% endif %}
                <form method="POST" action="/search" style="margin:0">
                    <input type="hidden" name="artist" value="{{ d.name }}">
                    <button type="submit" class="discovery-btn">Analyse</button>
                </form>
            </div>
            {% endfor %}
        </div>
    </div>
    {% endif %}

    <!-- MINI PLAYER -->
    <div class="mini-player" id="mini-player">
        <div class="player-info">
            <span class="player-title" id="player-title">-</span>
            <span class="player-artist" id="player-artist">-</span>
            <span class="player-note">30s preview via Deezer</span>
        </div>
        <audio id="audio-player" controls>
            <source id="audio-src" src="" type="audio/mp3">
        </audio>
        <button class="player-close" onclick="closeMiniPlayer()">✕</button>
    </div>

    <!-- FOOTER -->
    <div class="footer">
        <span class="footer-brand">Waveline</span>
        <span class="footer-sub">Built by Dimos Dimitrios Papageorgiou · Manchester · 2026</span>
    </div>

    <script>
    async function playTrack(artist, track) {
        var tag = event.target;
        tag.textContent = '⏳ ' + track;
        try {
            var resp = await fetch('/preview?artist=' + encodeURIComponent(artist) + '&track=' + encodeURIComponent(track));
            var data = await resp.json();
            if (data.preview_url) {
                document.getElementById('player-title').textContent = data.title;
                document.getElementById('player-artist').textContent = data.artist;
                var audio = document.getElementById('audio-player');
                document.getElementById('audio-src').src = data.preview_url;
                audio.load();
                audio.play();
                document.getElementById('mini-player').style.display = 'flex';
            } else {
                window.open('https://open.spotify.com/search/' + encodeURIComponent(artist + ' ' + track), '_blank');
            }
        } catch(e) {
            window.open('https://open.spotify.com/search/' + encodeURIComponent(artist + ' ' + track), '_blank');
        }
        tag.textContent = '▶ ' + track;
    }

    function closeMiniPlayer() {
        document.getElementById('audio-player').pause();
        document.getElementById('mini-player').style.display = 'none';
    }

    function toggleInsight(i, btn) {
        var body = document.getElementById('body-' + i);
        var preview = body.querySelector('.insight-preview');
        var full    = body.querySelector('.insight-full');
        if (full.style.display !== 'block') {
            preview.style.display = 'none';
            full.style.display    = 'block';
            btn.textContent = 'Show less';
        } else {
            preview.style.display = 'block';
            full.style.display    = 'none';
            btn.textContent = 'Read more';
        }
    }

    // ── DARK MODE ──
    function toggleDark() {
        var isDark = document.body.classList.toggle('dark');
        localStorage.setItem('dark', isDark ? '1' : '0');
        document.getElementById('dark-btn').textContent = isDark ? '☀' : '◑';
    }
    (function() {
        if (localStorage.getItem('dark') === '1') {
            document.body.classList.add('dark');
            var btn = document.getElementById('dark-btn');
            if (btn) btn.textContent = '☀';
        }
    })();

    // ── KEYBOARD SHORTCUT: / to focus search ──
    document.addEventListener('keydown', function(e) {
        if (e.key === '/' && document.activeElement.tagName !== 'INPUT') {
            e.preventDefault();
            document.getElementById('artist-input').focus();
        }
        if (e.key === 'Escape') {
            document.getElementById('suggestions').style.display = 'none';
        }
    });

    // ── SEARCH AUTOCOMPLETE ──
    var allArtists = [];
    fetch('/api/artists').then(r => r.json()).then(data => { allArtists = data; });

    var input = document.getElementById('artist-input');
    var sugBox = document.getElementById('suggestions');

    input.addEventListener('input', function() {
        var q = this.value.trim().toLowerCase();
        sugBox.innerHTML = '';
        if (!q || q.length < 1) { sugBox.style.display = 'none'; return; }
        var matches = allArtists.filter(a => a.toLowerCase().includes(q)).slice(0, 6);
        if (matches.length === 0) { sugBox.style.display = 'none'; return; }
        matches.forEach(function(name) {
            var div = document.createElement('div');
            div.className = 'suggestion';
            div.textContent = name;
            div.addEventListener('mousedown', function(e) {
                e.preventDefault();
                input.value = name;
                sugBox.style.display = 'none';
                document.getElementById('search-form').submit();
            });
            sugBox.appendChild(div);
        });
        sugBox.style.display = 'block';
    });

    input.addEventListener('blur', function() {
        setTimeout(function() { sugBox.style.display = 'none'; }, 150);
    });

    document.addEventListener('click', function(e) {
        if (!sugBox.contains(e.target) && e.target !== input) {
            sugBox.style.display = 'none';
        }
    });
    </script>

</body>
</html>
"""

def get_dashboard_data():
    conn = get_db_connection()
    cur = conn.cursor()

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

    # Latest releases for all searched artists
    cur.execute("SELECT DISTINCT artist_name FROM searches ORDER BY artist_name")
    all_artists = [r[0] for r in cur.fetchall()]
    discovery = get_discovery_artists(all_artists)

    cur.close()
    conn.close()

    return total_searches, unique_artists, searches_today, artist_plays, latest_insights, discovery

@app.route("/")
def dashboard():
    message = request.args.get("message")
    error = request.args.get("error")
    total_searches, unique_artists, searches_today, artist_plays, latest_insights, discovery = get_dashboard_data()
    from urllib.parse import quote
    return render_template_string(HTML_TEMPLATE,
        total_searches=total_searches,
        unique_artists=unique_artists,
        searches_today=searches_today,
        artist_plays=artist_plays,
        latest_insights=latest_insights,
        discovery=discovery,
        message=message,
        error=error,
        urlencode=quote
    )

@app.route("/api/artists")
def api_artists():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT artist_name FROM searches ORDER BY artist_name")
        artists = [row[0] for row in cur.fetchall()]
        cur.close()
        conn.close()
        from flask import jsonify
        return jsonify(artists)
    except Exception:
        from flask import jsonify
        return jsonify([])

@app.route("/preview")
def preview():
    artist = request.args.get("artist", "")
    track  = request.args.get("track", "")
    try:
        resp = http_requests.get(
            "https://api.deezer.com/search",
            params={"q": f"{artist} {track}", "limit": 1},
            timeout=5
        )
        data = resp.json()
        if data.get("total", 0) > 0:
            r = data["data"][0]
            return {"preview_url": r.get("preview", ""), "title": r.get("title", track), "artist": r.get("artist", {}).get("name", artist)}
    except Exception:
        pass
    return {"preview_url": "", "title": track, "artist": artist}

@app.route("/search", methods=["POST"])
def search():
    artist = request.form.get("artist", "").strip()
    if not artist:
        return redirect(url_for("dashboard", message="Please enter an artist name.", error=True))
    try:
        run_pipeline(artist)
        return redirect(url_for("dashboard", message=f"✅ {artist} analysed and saved successfully!"))
    except Exception as e:
        return redirect(url_for("dashboard", message=f"❌ Could not analyse {artist}: {str(e)}", error=True))

ARTIST_PROFILE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{{ artist_name }} — Waveline</title>
    <link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
    <style>
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Space Mono', monospace; background: #e2e2df; color: #111; }
        /* topbar */
        .topbar { background: #111; color: #fff; padding: 0 24px; height: 44px; display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 100; }
        .topbar-brand { font-weight: 700; font-size: 0.95em; letter-spacing: 2px; color: #1da0c3; }
        .topbar-right { display: flex; align-items: center; gap: 16px; }
        .topbar-link { color: #1da0c3; font-size: 0.78em; text-decoration: none; }
        .topbar-link:hover { color: #fff; }
        /* hero */
        .profile-hero { display: flex; align-items: center; gap: 28px; padding: 36px 36px 24px; background: #fff; border-bottom: 1px solid #e8e8e8; }
        .profile-img { width: 120px; height: 120px; object-fit: cover; border-radius: 4px; background: #eee; }
        .profile-img-placeholder { width: 120px; height: 120px; background: #1da0c3; border-radius: 4px; display: flex; align-items: center; justify-content: center; font-size: 2.5em; color: #fff; font-weight: 700; }
        .profile-meta { flex: 1; }
        .profile-name { font-size: 2em; font-weight: 700; color: #111; line-height: 1.1; }
        .profile-sources { display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
        .src-badge { font-size: 0.7em; padding: 3px 10px; border-radius: 2px; font-weight: 700; }
        .src-badge.lastfm { background: #d51007; color: #fff; }
        .src-badge.deezer { background: #a238ff; color: #fff; }
        .src-badge.spotify { background: #1db954; color: #fff; }
        .genre-wrap { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
        .genre-tag { font-size: 0.68em; padding: 3px 10px; border: 1px solid #1db954; color: #1db954; border-radius: 2px; }
        .spotify-link { display: inline-block; margin-top: 12px; font-size: 0.72em; color: #1db954; text-decoration: none; border: 1px solid #1db954; padding: 4px 12px; border-radius: 2px; }
        .spotify-link:hover { background: #1db954; color: #fff; }
        .profile-searches { font-size: 0.75em; color: #aaa; margin-top: 8px; }
        .back-link { font-size: 0.78em; color: #1da0c3; text-decoration: none; border: 1px solid #1da0c3; padding: 5px 12px; border-radius: 2px; }
        .back-link:hover { background: #1da0c3; color: #fff; }
        /* layout */
        .profile-body { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; padding: 28px 36px; max-width: 1100px; margin: 0 auto; }
        .card { background: #fff; border: 1px solid #e8e8e8; padding: 22px 24px; }
        .card-title { font-size: 0.72em; color: #1da0c3; letter-spacing: 1.5px; text-transform: uppercase; border-bottom: 1px solid #f0f0f0; padding-bottom: 10px; margin-bottom: 16px; }
        .insight-text { font-size: 0.8em; line-height: 1.85; color: #333; }
        /* tracks */
        .track-row { display: flex; align-items: center; justify-content: space-between; padding: 9px 0; border-bottom: 1px solid #f5f5f3; }
        .track-row:last-child { border-bottom: none; }
        .track-name { font-size: 0.8em; color: #222; cursor: pointer; }
        .track-name:hover { color: #1da0c3; }
        .track-plays { font-size: 0.72em; color: #aaa; }
        .play-btn { font-size: 0.75em; color: #1da0c3; cursor: pointer; border: 1px solid #1da0c3; padding: 2px 8px; border-radius: 2px; background: none; }
        .play-btn:hover { background: #1da0c3; color: #fff; }
        /* similar chips */
        .chip-wrap { display: flex; flex-wrap: wrap; gap: 8px; }
        .chip { font-size: 0.75em; padding: 5px 12px; border: 1px solid #ddd; color: #444; cursor: pointer; border-radius: 2px; text-decoration: none; }
        .chip:hover { border-color: #1da0c3; color: #1da0c3; }
        /* stats row */
        .stat-row { display: flex; justify-content: space-around; padding: 20px 36px; background: #111; }
        .stat-item { text-align: center; }
        .stat-label { font-size: 0.65em; color: #1da0c3; letter-spacing: 1px; text-transform: uppercase; }
        .stat-val { font-size: 1.6em; font-weight: 700; color: #fff; }
        /* mini player */
        #mini-player { display: none; position: fixed; bottom: 0; left: 0; right: 0; background: #111; padding: 10px 20px; flex-direction: row; align-items: center; gap: 16px; z-index: 999; border-top: 2px solid #1da0c3; }
        #player-title { font-size: 0.8em; color: #fff; font-weight: 700; }
        #player-artist { font-size: 0.72em; color: #888; }
        audio { flex: 1; height: 28px; }
        .close-player { color: #555; font-size: 1.1em; cursor: pointer; padding: 4px 8px; }
        .close-player:hover { color: #fff; }
        /* dark mode */
        body.dark { background: #0d0d0d; color: #e0e0e0; }
        body.dark .profile-hero, body.dark .card { background: #111; border-color: #222; }
        body.dark .profile-name { color: #fff; }
        body.dark .insight-text { color: #ccc; }
        body.dark .track-row { border-color: #1a1a1a; }
        body.dark .track-name { color: #ddd; }
        body.dark .chip { border-color: #333; color: #999; }
        body.dark .card-title { border-color: #222; }
        @media (max-width: 700px) {
            .topbar { padding: 0 14px; }
            .topbar-right { gap: 8px; }
            .topbar-link { font-size: 0.62em; }
            .profile-hero { flex-direction: column; padding: 20px 16px; gap: 16px; }
            .profile-img, .profile-img-placeholder { width: 90px; height: 90px; }
            .profile-name { font-size: 1.5em; }
            .stat-row { flex-wrap: wrap; gap: 0; padding: 12px 8px; }
            .stat-item { width: 50%; padding: 10px 0; text-align: center; }
            .profile-body { grid-template-columns: 1fr; padding: 16px; gap: 16px; }
            .card { padding: 16px; }
            #mini-player { flex-wrap: wrap; padding: 8px 12px; gap: 8px; }
            #mini-player audio { width: 100%; }
        }
    </style>
</head>
<body>

<div class="topbar">
    <span class="topbar-brand">WAVELINE</span>
    <div class="topbar-right">
        <a href="/compare" class="topbar-link">Compare</a>
        <a href="/profile" class="topbar-link">Taste Profile</a>
        <button id="dark-btn" onclick="toggleDark()" style="background:none;border:1px solid #333;color:#888;font-size:0.85em;padding:3px 8px;cursor:pointer;border-radius:3px;">◑</button>
    </div>
</div>

<div class="profile-hero">
    {% if spotify.get('image') %}
    <img src="{{ spotify.image }}" class="profile-img" alt="{{ artist_name }}">
    {% elif deezer_image %}
    <img src="{{ deezer_image }}" class="profile-img" alt="{{ artist_name }}">
    {% else %}
    <div class="profile-img-placeholder">{{ artist_name[0] | upper }}</div>
    {% endif %}
    <div class="profile-meta">
        <div class="profile-name">{{ artist_name }}</div>
        <div class="profile-sources">
            {% if lastfm_listeners %}<span class="src-badge lastfm">Last.fm · {{ "{:,}".format(lastfm_listeners) }} listeners</span>{% endif %}
            {% if deezer_fans %}<span class="src-badge deezer">Deezer · {{ "{:,}".format(deezer_fans) }} fans</span>{% endif %}
            {% if spotify.get('followers') %}<span class="src-badge spotify">Spotify · {{ "{:,}".format(spotify.followers) }} followers</span>{% endif %}
        </div>
        {% if spotify.get('genres') %}
        <div class="genre-wrap">
            {% for g in spotify.genres %}<span class="genre-tag">{{ g }}</span>{% endfor %}
        </div>
        {% endif %}
        <div class="profile-searches">Searched {{ search_count }} time{{ 's' if search_count != 1 else '' }} in your pipeline · Last: {{ last_searched }}</div>
        {% if spotify.get('spotify_url') %}<a href="{{ spotify.spotify_url }}" target="_blank" class="spotify-link">▶ Open on Spotify</a>{% endif %}
    </div>
    <a href="/" class="back-link">← Back</a>
</div>

<div class="stat-row">
    <div class="stat-item">
        <div class="stat-label">Last.fm Listeners</div>
        <div class="stat-val">{{ "{:,}".format(lastfm_listeners) if lastfm_listeners else "—" }}</div>
    </div>
    <div class="stat-item">
        <div class="stat-label">Total Scrobbles</div>
        <div class="stat-val">{% if lastfm_scrobbles > 1000000 %}{{ "%.1fM" | format(lastfm_scrobbles / 1000000) }}{% elif lastfm_scrobbles %}{{ "{:,}".format(lastfm_scrobbles) }}{% else %}—{% endif %}</div>
    </div>
    <div class="stat-item">
        <div class="stat-label">Deezer Fans</div>
        <div class="stat-val">{% if deezer_fans > 1000000 %}{{ "%.1fM" | format(deezer_fans / 1000000) }}{% elif deezer_fans %}{{ "{:,}".format(deezer_fans) }}{% else %}—{% endif %}</div>
    </div>
    <div class="stat-item">
        <div class="stat-label">Top Track Plays</div>
        <div class="stat-val">{{ top_playcount }}</div>
    </div>
    <div class="stat-item">
        <div class="stat-label">Spotify Popularity</div>
        <div class="stat-val">{{ spotify.popularity if spotify.get('popularity') else "—" }}<span style="font-size:0.5em;color:#888;">/100</span></div>
    </div>
</div>

<div class="profile-body">
    <!-- Claude Insight -->
    <div class="card" style="grid-column: 1 / -1;">
        <div class="card-title">Artist Insight</div>
        <div class="insight-text">{{ insight }}</div>
    </div>

    <!-- Top Tracks -->
    <div class="card">
        <div class="card-title">Top Tracks</div>
        {% for track in tracks %}
        <div class="track-row">
            <span class="track-name" onclick="playTrack('{{ artist_name }}', '{{ track.name }}')">▶ {{ track.name }}</span>
            <span class="track-plays">{{ "{:,}".format(track.plays) }} plays</span>
        </div>
        {% endfor %}
    </div>

    <!-- Similar Artists -->
    <div class="card">
        <div class="card-title">Similar Artists</div>
        <div class="chip-wrap">
            {% for name in similar_artists %}
            <a href="/artist/{{ name | urlencode }}" class="chip">{{ name }}</a>
            {% endfor %}
        </div>
    </div>
</div>

<!-- Mini Player -->
<div id="mini-player">
    <div>
        <div id="player-title">—</div>
        <div id="player-artist">—</div>
    </div>
    <audio id="audio-player" controls>
        <source id="audio-src" src="" type="audio/mpeg">
    </audio>
    <span class="close-player" onclick="closeMiniPlayer()">✕</span>
</div>

<script>
    function toggleDark() {
        var isDark = document.body.classList.toggle('dark');
        localStorage.setItem('dark', isDark ? '1' : '0');
        document.getElementById('dark-btn').textContent = isDark ? '☀' : '◑';
    }
    (function() {
        if (localStorage.getItem('dark') === '1') {
            document.body.classList.add('dark');
            var btn = document.getElementById('dark-btn');
            if (btn) btn.textContent = '☀';
        }
    })();

    async function playTrack(artist, track) {
        try {
            var resp = await fetch('/preview?artist=' + encodeURIComponent(artist) + '&track=' + encodeURIComponent(track));
            var data = await resp.json();
            if (data.preview_url) {
                document.getElementById('player-title').textContent = data.title;
                document.getElementById('player-artist').textContent = data.artist;
                var audio = document.getElementById('audio-player');
                document.getElementById('audio-src').src = data.preview_url;
                audio.load(); audio.play();
                document.getElementById('mini-player').style.display = 'flex';
            } else {
                window.open('https://open.spotify.com/search/' + encodeURIComponent(artist + ' ' + track), '_blank');
            }
        } catch(e) {
            window.open('https://open.spotify.com/search/' + encodeURIComponent(artist + ' ' + track), '_blank');
        }
    }
    function closeMiniPlayer() {
        document.getElementById('audio-player').pause();
        document.getElementById('mini-player').style.display = 'none';
    }
</script>
</body>
</html>
"""

@app.route("/artist/<path:artist_name>")
def artist_profile(artist_name):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT artist_name, claude_insight, searched_at, top_tracks
            FROM searches
            WHERE LOWER(artist_name) = LOWER(%s)
            ORDER BY searched_at DESC LIMIT 1
        """, (artist_name,))
        row = cur.fetchone()
        if not row:
            cur.close(); conn.close()
            # Artist not in DB — run the pipeline automatically, then reload
            try:
                run_pipeline(artist_name)
                return redirect(url_for("artist_profile", artist_name=artist_name))
            except Exception as e:
                return render_template_string("""
<!DOCTYPE html><html><head><meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>body{font-family:'Space Mono',monospace;background:#f5f5f3;padding:60px 36px;}
.topbar{background:#111;color:#fff;padding:0 24px;height:44px;display:flex;align-items:center;position:fixed;top:0;left:0;right:0;}
.topbar-brand{font-weight:700;font-size:0.95em;letter-spacing:2px;color:#1da0c3;}
.box{background:#fff;border:1px solid #e8e8e8;padding:28px 32px;max-width:500px;margin:60px auto;}
h2{font-size:1em;color:#111;margin-bottom:12px;}
p{font-size:0.8em;color:#666;line-height:1.7;}
a{color:#1da0c3;font-size:0.8em;}
</style></head><body>
<div class="topbar"><span class="topbar-brand">WAVELINE</span></div>
<div class="box">
<h2>Could not load {{ name }}</h2>
<p>{{ error }}</p>
<a href="/">← Back to dashboard</a>
</div></body></html>
""", name=artist_name, error=str(e)), 500

        cur.execute("SELECT COUNT(*) FROM searches WHERE LOWER(artist_name) = LOWER(%s)", (artist_name,))
        search_count = cur.fetchone()[0]
        cur.close(); conn.close()

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
                deezer_image = d["data"][0].get("picture_medium", "")
                deezer_fans = d["data"][0].get("nb_fan", 0)
        except Exception:
            pass

        # Spotify: genres, popularity, followers
        spotify = get_spotify_artist(name)

        # Last.fm: listeners + total scrobbles
        lastfm_listeners = 0
        lastfm_scrobbles = 0
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
        except Exception:
            pass

        from urllib.parse import quote
        return render_template_string(
            ARTIST_PROFILE_TEMPLATE,
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
            spotify=spotify,
            urlencode=quote
        )
    except Exception as e:
        return f"<h2>Error: {e}</h2><a href='/'>Back</a>", 500

# ──────────────────────────────────────────────
#  TASTE PROFILE  /profile
# ──────────────────────────────────────────────

_taste_cache = {}

TASTE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Your Taste Profile — Waveline</title>
    <link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
    <style>
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Space Mono', monospace; background: #e2e2df; color: #111; }
        .topbar { background: #111; color: #fff; padding: 0 24px; height: 44px; display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 100; }
        .topbar-brand { font-weight: 700; font-size: 0.95em; letter-spacing: 2px; color: #1da0c3; }
        .topbar-right { display: flex; align-items: center; gap: 16px; }
        .topbar-link { color: #1da0c3; font-size: 0.78em; text-decoration: none; }
        .topbar-link:hover { color: #fff; }
        .hero { background: #fff; border-bottom: 1px solid #e8e8e8; padding: 32px 36px; }
        .hero h1 { font-size: 1.4em; font-weight: 700; color: #111; }
        .hero p { font-size: 0.8em; color: #888; margin-top: 6px; }
        .body { max-width: 860px; margin: 0 auto; padding: 28px 36px; }
        .card { background: #fff; border: 1px solid #e8e8e8; padding: 24px 28px; margin-bottom: 20px; }
        .card-title { font-size: 0.7em; color: #1da0c3; letter-spacing: 1.5px; text-transform: uppercase; border-bottom: 1px solid #f0f0f0; padding-bottom: 10px; margin-bottom: 16px; }
        .taste-text { font-size: 0.82em; line-height: 1.9; color: #333; }
        .artist-grid { display: flex; flex-wrap: wrap; gap: 10px; }
        .artist-chip { font-size: 0.75em; padding: 6px 14px; border: 1px solid #ddd; color: #444; border-radius: 2px; text-decoration: none; }
        .artist-chip:hover { border-color: #1da0c3; color: #1da0c3; }
        .loading { text-align: center; padding: 60px; font-size: 0.82em; color: #aaa; }
        .refresh-btn { font-size: 0.75em; border: 1px solid #1da0c3; color: #1da0c3; background: none; padding: 6px 14px; cursor: pointer; border-radius: 2px; margin-top: 12px; }
        .refresh-btn:hover { background: #1da0c3; color: #fff; }
        body.dark { background: #0d0d0d; color: #e0e0e0; }
        body.dark .hero, body.dark .card { background: #111; border-color: #222; }
        body.dark .hero h1 { color: #fff; }
        body.dark .taste-text { color: #ccc; }
        body.dark .artist-chip { border-color: #333; color: #999; }
        body.dark .card-title { border-color: #222; }
        @media (max-width: 700px) {
            .topbar { padding: 0 14px; }
            .topbar-right { gap: 10px; }
            .topbar-link { font-size: 0.65em; }
            .hero { padding: 20px 16px; }
            .body { padding: 16px; }
            .artist-grid { gap: 8px; }
        }
    </style>
</head>
<body>
<div class="topbar">
    <span class="topbar-brand">WAVELINE</span>
    <div class="topbar-right">
        <a href="/" class="topbar-link">Dashboard</a>
        <a href="/compare" class="topbar-link">Compare</a>
        <button id="dark-btn" onclick="toggleDark()" style="background:none;border:1px solid #333;color:#888;font-size:0.85em;padding:3px 8px;cursor:pointer;border-radius:3px;">◑</button>
    </div>
</div>
<div class="hero">
    <h1>Your Taste Profile</h1>
    <p>Based on {{ artist_count }} artists in your pipeline</p>
</div>
<div class="body">
    <div class="card">
        <div class="card-title">Your Taste Profile</div>
        <div class="taste-text">{{ taste_analysis }}</div>
        <form method="POST" action="/profile/refresh" style="margin-top:16px;">
            <button type="submit" class="refresh-btn">↻ Refresh analysis</button>
        </form>
    </div>
    <div class="card">
        <div class="card-title">Artists in Your Pipeline</div>
        <div class="artist-grid">
            {% for name in artists %}
            <a href="/artist/{{ name | urlencode }}" class="artist-chip">{{ name }}</a>
            {% endfor %}
        </div>
    </div>
</div>
<script>
    function toggleDark() {
        var isDark = document.body.classList.toggle('dark');
        localStorage.setItem('dark', isDark ? '1' : '0');
        document.getElementById('dark-btn').textContent = isDark ? '☀' : '◑';
    }
    (function() {
        if (localStorage.getItem('dark') === '1') {
            document.body.classList.add('dark');
            var btn = document.getElementById('dark-btn');
            if (btn) btn.textContent = '☀';
        }
    })();
</script>
</body>
</html>
"""

@app.route("/profile")
def taste_profile():
    import anthropic as _anthropic
    from urllib.parse import quote
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT ON (artist_name) artist_name, top_tracks
            FROM searches ORDER BY artist_name, searched_at DESC
        """)
        rows = cur.fetchall()
        cur.close(); conn.close()
    except Exception as e:
        return f"<h2>DB error: {e}</h2>", 500

    artists = [r[0] for r in rows]
    if not artists:
        return render_template_string(TASTE_TEMPLATE, taste_analysis="No artists in your pipeline yet — search some first!", artists=[], artist_count=0, urlencode=quote)

    cache_key = ",".join(sorted(artists))
    if cache_key in _taste_cache:
        analysis = _taste_cache[cache_key]
    else:
        # Build a summary of artists + top tracks
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

    return render_template_string(TASTE_TEMPLATE, taste_analysis=analysis, artists=artists, artist_count=len(artists), urlencode=quote)

@app.route("/profile/refresh", methods=["POST"])
def taste_profile_refresh():
    _taste_cache.clear()
    return redirect("/profile")

# ──────────────────────────────────────────────
#  ARTIST COMPARISON  /compare
# ──────────────────────────────────────────────

COMPARE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Compare Artists — Waveline</title>
    <link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
    <style>
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Space Mono', monospace; background: #e2e2df; color: #111; }
        .topbar { background: #111; color: #fff; padding: 0 24px; height: 44px; display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 100; }
        .topbar-brand { font-weight: 700; font-size: 0.95em; letter-spacing: 2px; color: #1da0c3; }
        .topbar-right { display: flex; align-items: center; gap: 16px; }
        .topbar-link { color: #1da0c3; font-size: 0.78em; text-decoration: none; }
        .topbar-link:hover { color: #fff; }
        .hero { background: #fff; border-bottom: 1px solid #e8e8e8; padding: 28px 36px; }
        .hero h1 { font-size: 1.3em; font-weight: 700; }
        .compare-form { display: flex; gap: 12px; margin-top: 16px; flex-wrap: wrap; }
        .compare-form input { font-family: 'Space Mono', monospace; font-size: 0.82em; padding: 10px 16px; border: 1px solid #ddd; background: #fafafa; flex: 1; min-width: 180px; }
        .compare-form input:focus { outline: none; border-color: #1da0c3; }
        .compare-form button { font-family: 'Space Mono', monospace; font-size: 0.82em; padding: 10px 22px; background: #111; color: #fff; border: none; cursor: pointer; }
        .compare-form button:hover { background: #1da0c3; }
        .body { max-width: 1060px; margin: 0 auto; padding: 28px 36px; }
        .vs-row { display: grid; grid-template-columns: 1fr 52px 1fr; gap: 0; align-items: stretch; margin-bottom: 24px; }
        .vs-divider { display: flex; align-items: center; justify-content: center; font-size: 1.1em; font-weight: 700; color: #1da0c3; background: #e2e2df; border-top: 1px solid #e8e8e8; border-bottom: 1px solid #e8e8e8; }
        .card { background: #fff; border: 1px solid #e8e8e8; overflow: hidden; }
        .card-img { width: 100%; height: 160px; object-fit: cover; display: block; }
        .card-img-placeholder { width: 100%; height: 160px; background: linear-gradient(135deg, #1da0c3 0%, #0d7a9a 100%); display: flex; align-items: center; justify-content: center; font-size: 3em; color: #fff; font-weight: 700; }
        .card-body { padding: 20px 24px; }
        .card-label { font-size: 0.62em; color: #1da0c3; letter-spacing: 2px; text-transform: uppercase; margin-bottom: 6px; }
        .card-title { font-size: 0.7em; color: #1da0c3; letter-spacing: 1.5px; text-transform: uppercase; border-bottom: 1px solid #f0f0f0; padding-bottom: 10px; margin-bottom: 14px; }
        .artist-heading { font-size: 1.25em; font-weight: 700; color: #111; margin-bottom: 12px; line-height: 1.2; }
        .insight-text { font-size: 0.8em; line-height: 1.85; color: #555; border-left: 2px solid #1da0c3; padding-left: 12px; margin-bottom: 16px; }
        .track-item { display: flex; align-items: center; justify-content: space-between; font-size: 0.78em; padding: 7px 0; border-bottom: 1px solid #f5f5f3; color: #444; }
        .track-item:last-child { border-bottom: none; }
        .track-play-btn { font-size: 0.72em; color: #1da0c3; cursor: pointer; border: 1px solid #1da0c3; padding: 2px 8px; border-radius: 2px; background: none; white-space: nowrap; }
        .track-play-btn:hover { background: #1da0c3; color: #fff; }
        .spotify-btn { display: inline-flex; align-items: center; gap: 6px; margin-top: 14px; font-size: 0.72em; color: #1db954; text-decoration: none; border: 1px solid #1db954; padding: 5px 12px; border-radius: 2px; }
        .spotify-btn:hover { background: #1db954; color: #fff; }
        .verdict-card { background: #fff; border: 1px solid #e8e8e8; border-top: 3px solid #1da0c3; padding: 28px 32px; }
        .verdict-header { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
        .verdict-icon { font-size: 1.2em; }
        .verdict-title { font-size: 0.7em; color: #1da0c3; letter-spacing: 2px; text-transform: uppercase; }
        .verdict-text { font-size: 0.84em; line-height: 2; color: #333; }
        .no-data { font-size: 0.8em; color: #aaa; }
        /* mini player */
        #mini-player { display: none; position: fixed; bottom: 0; left: 0; right: 0; background: #111; padding: 10px 20px; flex-direction: row; align-items: center; gap: 16px; z-index: 999; border-top: 2px solid #1da0c3; }
        #player-title { font-size: 0.8em; color: #fff; font-weight: 700; }
        #player-artist { font-size: 0.72em; color: #888; }
        audio { flex: 1; height: 28px; }
        .close-player { color: #555; font-size: 1.1em; cursor: pointer; padding: 4px 8px; }
        .close-player:hover { color: #fff; }
        body.dark { background: #0d0d0d; color: #e0e0e0; }
        body.dark .hero, body.dark .card, body.dark .verdict-card { background: #111; border-color: #222; }
        body.dark .verdict-card { border-top-color: #1da0c3; }
        body.dark .vs-divider { background: #0d0d0d; border-color: #222; }
        body.dark .hero h1, body.dark .artist-heading { color: #fff; }
        body.dark .insight-text { color: #aaa; border-color: #1da0c3; }
        body.dark .verdict-text { color: #ccc; }
        body.dark .track-item { color: #999; border-color: #1a1a1a; }
        body.dark .compare-form input { background: #1a1a1a; border-color: #333; color: #e0e0e0; }
        @media (max-width: 700px) {
            .topbar { padding: 0 14px; }
            .topbar-right { gap: 8px; }
            .topbar-link { font-size: 0.62em; }
            .hero { padding: 20px 16px; }
            .compare-form { flex-direction: column; }
            .compare-form input, .compare-form button { width: 100%; }
            .body { padding: 16px; }
            .vs-row { grid-template-columns: 1fr; }
            .vs-divider { height: 44px; border: none; border-top: 1px solid #e8e8e8; border-bottom: 1px solid #e8e8e8; }
            .verdict-card { padding: 18px 16px; }
            #mini-player { flex-wrap: wrap; padding: 8px 12px; gap: 8px; }
            #mini-player audio { width: 100%; }
        }
    </style>
</head>
<body>
<div class="topbar">
    <span class="topbar-brand">WAVELINE</span>
    <div class="topbar-right">
        <a href="/" class="topbar-link">Dashboard</a>
        <a href="/profile" class="topbar-link">Taste Profile</a>
        <button id="dark-btn" onclick="toggleDark()" style="background:none;border:1px solid #333;color:#888;font-size:0.85em;padding:3px 8px;cursor:pointer;border-radius:3px;">◑</button>
    </div>
</div>
<div class="hero">
    <h1>Compare Artists</h1>
    <form method="GET" action="/compare" class="compare-form">
        <input type="text" name="a" placeholder="First artist" value="{{ a or '' }}" required>
        <input type="text" name="b" placeholder="Second artist" value="{{ b or '' }}" required>
        <button type="submit">Compare</button>
    </form>
</div>
<div class="body">
{% if a_data and b_data %}
<div class="vs-row">
    <!-- Artist A -->
    <div class="card">
        {% if a_data.image %}<img src="{{ a_data.image }}" class="card-img" alt="{{ a_data.name }}">
        {% else %}<div class="card-img-placeholder">{{ a_data.name[0] | upper }}</div>{% endif %}
        <div class="card-body">
            <div class="card-label">Artist A</div>
            <div class="artist-heading">{{ a_data.name }}</div>
            <div class="insight-text">{{ a_data.insight[:280] }}…</div>
            {% for t in a_data.tracks %}
            <div class="track-item">
                <span>{{ t }}</span>
                <button class="track-play-btn" onclick="playTrack('{{ a_data.name }}', '{{ t }}')">▶ Play</button>
            </div>
            {% endfor %}
            {% if a_data.spotify_url %}
            <a href="{{ a_data.spotify_url }}" target="_blank" class="spotify-btn">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="#1db954"><path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z"/></svg>
                Open on Spotify
            </a>
            {% endif %}
        </div>
    </div>

    <!-- VS -->
    <div class="vs-divider">VS</div>

    <!-- Artist B -->
    <div class="card">
        {% if b_data.image %}<img src="{{ b_data.image }}" class="card-img" alt="{{ b_data.name }}">
        {% else %}<div class="card-img-placeholder">{{ b_data.name[0] | upper }}</div>{% endif %}
        <div class="card-body">
            <div class="card-label">Artist B</div>
            <div class="artist-heading">{{ b_data.name }}</div>
            <div class="insight-text">{{ b_data.insight[:280] }}…</div>
            {% for t in b_data.tracks %}
            <div class="track-item">
                <span>{{ t }}</span>
                <button class="track-play-btn" onclick="playTrack('{{ b_data.name }}', '{{ t }}')">▶ Play</button>
            </div>
            {% endfor %}
            {% if b_data.spotify_url %}
            <a href="{{ b_data.spotify_url }}" target="_blank" class="spotify-btn">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="#1db954"><path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z"/></svg>
                Open on Spotify
            </a>
            {% endif %}
        </div>
    </div>
</div>

<div class="verdict-card">
    <div class="verdict-header">
        <span class="verdict-icon">◈</span>
        <span class="verdict-title">Sound Analysis</span>
    </div>
    <div class="verdict-text">{{ verdict }}</div>
</div>
{% elif a or b %}
<div class="card"><p class="no-data">Fetching artist data — if this persists, try again in a moment.</p></div>
{% endif %}
</div>

<!-- Mini Player -->
<div id="mini-player">
    <div>
        <div id="player-title">—</div>
        <div id="player-artist">—</div>
    </div>
    <audio id="audio-player" controls>
        <source id="audio-src" src="" type="audio/mpeg">
    </audio>
    <span class="close-player" onclick="closeMiniPlayer()">✕</span>
</div>

<script>
    async function playTrack(artist, track) {
        try {
            var resp = await fetch('/preview?artist=' + encodeURIComponent(artist) + '&track=' + encodeURIComponent(track));
            var data = await resp.json();
            if (data.preview_url) {
                document.getElementById('player-title').textContent = data.title;
                document.getElementById('player-artist').textContent = data.artist;
                var audio = document.getElementById('audio-player');
                document.getElementById('audio-src').src = data.preview_url;
                audio.load(); audio.play();
                document.getElementById('mini-player').style.display = 'flex';
            } else {
                window.open('https://open.spotify.com/search/' + encodeURIComponent(artist + ' ' + track), '_blank');
            }
        } catch(e) {
            window.open('https://open.spotify.com/search/' + encodeURIComponent(artist + ' ' + track), '_blank');
        }
    }
    function closeMiniPlayer() {
        document.getElementById('audio-player').pause();
        document.getElementById('mini-player').style.display = 'none';
    }
    function toggleDark() {
        var isDark = document.body.classList.toggle('dark');
        localStorage.setItem('dark', isDark ? '1' : '0');
        document.getElementById('dark-btn').textContent = isDark ? '☀' : '◑';
    }
    (function() {
        if (localStorage.getItem('dark') === '1') {
            document.body.classList.add('dark');
            var btn = document.getElementById('dark-btn');
            if (btn) btn.textContent = '☀';
        }
    })();
</script>
</body>
</html>
"""

def get_artist_db(name):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT artist_name, claude_insight, top_tracks FROM searches
            WHERE LOWER(artist_name) = LOWER(%s)
            ORDER BY searched_at DESC LIMIT 1
        """, (name,))
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row: return None
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

@app.route("/compare")
def compare():
    import anthropic as _anthropic
    a = request.args.get("a", "").strip()
    b = request.args.get("b", "").strip()

    # Auto-fetch any artist not yet in the DB
    if a and not get_artist_db(a):
        try: run_pipeline(a)
        except Exception: pass
    if b and not get_artist_db(b):
        try: run_pipeline(b)
        except Exception: pass

    a_data = get_artist_db(a) if a else None
    b_data = get_artist_db(b) if b else None
    verdict = ""
    if a_data and b_data:
        prompt = f"""Compare these two artists:

{a_data['name']}: top tracks — {', '.join(a_data['tracks'])}
Insight: {a_data['insight'][:400]}

{b_data['name']}: top tracks — {', '.join(b_data['tracks'])}
Insight: {b_data['insight'][:400]}

Write a 2-paragraph comparison in plain prose. Cover: how their sounds and appeal differ, what they share, and which type of listener would prefer each. No markdown, no bullet points."""
        try:
            _client = _anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            msg = _client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=500, messages=[{"role": "user", "content": prompt}])
            verdict = msg.content[0].text
        except Exception as e:
            verdict = f"Could not generate comparison: {e}"
    return render_template_string(COMPARE_TEMPLATE, a=a, b=b, a_data=a_data, b_data=b_data, verdict=verdict)

# ──────────────────────────────────────────────
#  MUSIC NEWS  /news
# ──────────────────────────────────────────────

import xml.etree.ElementTree as ET
from datetime import timezone

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
    import time
    if _news_cache["data"] and time.time() - _news_cache["fetched_at"] < 3600:
        return _news_cache["data"]
    articles = []
    for feed in RSS_FEEDS:
        articles.extend(fetch_rss(feed))
    releases = get_spotify_new_releases()
    _news_cache["data"] = {"articles": articles, "releases": releases}
    _news_cache["fetched_at"] = time.time()
    return _news_cache["data"]

NEWS_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Music News — Waveline</title>
    <link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
    <style>
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Space Mono', monospace; background: #e2e2df; color: #111; }
        .topbar { background: #111; color: #fff; padding: 0 24px; height: 44px; display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 100; }
        .topbar-brand { font-weight: 700; font-size: 0.95em; letter-spacing: 2px; color: #1da0c3; }
        .topbar-right { display: flex; align-items: center; gap: 16px; }
        .topbar-link { color: #1da0c3; font-size: 0.78em; text-decoration: none; }
        .topbar-link:hover { color: #fff; }
        .hero { background: #fff; border-bottom: 1px solid #e8e8e8; padding: 28px 36px; display: flex; align-items: center; justify-content: space-between; }
        .hero h1 { font-size: 1.3em; font-weight: 700; }
        .hero p { font-size: 0.75em; color: #888; margin-top: 4px; }
        .refresh-btn { font-size: 0.72em; border: 1px solid #ddd; color: #888; background: none; padding: 6px 14px; cursor: pointer; border-radius: 2px; }
        .refresh-btn:hover { border-color: #1da0c3; color: #1da0c3; }
        .layout { display: grid; grid-template-columns: 1fr 380px; gap: 0; min-height: calc(100vh - 100px); }

        /* ── NEWS ARTICLES ── */
        .news-col { padding: 28px 32px; border-right: 1px solid #e8e8e8; }
        .section-title { font-size: 0.68em; color: #1da0c3; letter-spacing: 2px; text-transform: uppercase; margin-bottom: 20px; }
        .article { background: #fff; border: 1px solid #e8e8e8; padding: 16px 20px; margin-bottom: 12px; display: flex; flex-direction: column; gap: 6px; transition: border-color 0.15s; }
        .article:hover { border-color: #1da0c3; }
        .article-source { font-size: 0.62em; font-weight: 700; padding: 2px 8px; border-radius: 2px; color: #fff; display: inline-block; width: fit-content; }
        .article-title { font-size: 0.8em; color: #111; text-decoration: none; line-height: 1.5; }
        .article-title:hover { color: #1da0c3; }
        .article-meta { font-size: 0.65em; color: #aaa; }
        .no-articles { font-size: 0.8em; color: #aaa; padding: 20px 0; }

        /* ── NEW RELEASES ── */
        .releases-col { padding: 28px 24px; background: #fafaf8; }
        .releases-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
        .release-card { background: #fff; border: 1px solid #e8e8e8; overflow: hidden; transition: border-color 0.15s; text-decoration: none; display: block; }
        .release-card:hover { border-color: #1db954; }
        .release-card img { width: 100%; aspect-ratio: 1; object-fit: cover; display: block; }
        .release-card .no-img { width: 100%; aspect-ratio: 1; background: #1da0c3; display: flex; align-items: center; justify-content: center; font-size: 1.5em; color: #fff; }
        .release-info { padding: 8px 10px; }
        .release-name { font-size: 0.7em; font-weight: 700; color: #111; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .release-artist { font-size: 0.65em; color: #888; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 2px; }
        .release-type { font-size: 0.6em; color: #1db954; margin-top: 2px; }

        /* ── DARK ── */
        body.dark { background: #0d0d0d; color: #e0e0e0; }
        body.dark .hero, body.dark .article, body.dark .release-card { background: #111; border-color: #222; }
        body.dark .hero h1 { color: #fff; }
        body.dark .news-col { border-color: #222; }
        body.dark .releases-col { background: #0a0a0a; }
        body.dark .article-title { color: #e0e0e0; }
        body.dark .release-name { color: #e0e0e0; }
        @media (max-width: 700px) {
            .topbar { padding: 0 14px; }
            .topbar-right { gap: 8px; }
            .topbar-link { font-size: 0.62em; }
            .hero { padding: 20px 16px; flex-direction: column; gap: 10px; align-items: flex-start; }
            .layout { grid-template-columns: 1fr; }
            .news-col { border-right: none; padding: 16px; }
            .releases-col { padding: 16px; }
            .releases-grid { grid-template-columns: repeat(2, 1fr); gap: 10px; }
        }
    </style>
</head>
<body>
<div class="topbar">
    <span class="topbar-brand">WAVELINE</span>
    <div class="topbar-right">
        <a href="/" class="topbar-link">Dashboard</a>
        <a href="/profile" class="topbar-link">Taste Profile</a>
        <a href="/compare" class="topbar-link">Compare</a>
        <button id="dark-btn" onclick="toggleDark()" style="background:none;border:1px solid #333;color:#888;font-size:0.85em;padding:3px 8px;cursor:pointer;border-radius:3px;">◑</button>
    </div>
</div>
<div class="hero">
    <div>
        <h1>Music News</h1>
        <p>Latest from Pitchfork, NME, The Guardian & RA · New releases via Spotify</p>
    </div>
    <form method="POST" action="/news/refresh">
        <button type="submit" class="refresh-btn">↻ Refresh</button>
    </form>
</div>
<div class="layout">

    <!-- LEFT: NEWS ARTICLES -->
    <div class="news-col">
        <div class="section-title">Latest Music News</div>
        {% if articles %}
            {% for a in articles %}
            <div class="article">
                <span class="article-source" style="background:{{ a.color }}">{{ a.source }}</span>
                <a href="{{ a.link }}" target="_blank" class="article-title">{{ a.title }}</a>
                {% if a.pub %}<span class="article-meta">{{ a.pub }}</span>{% endif %}
            </div>
            {% endfor %}
        {% else %}
            <p class="no-articles">Could not load articles — check your connection and try refreshing.</p>
        {% endif %}
    </div>

    <!-- RIGHT: NEW RELEASES -->
    <div class="releases-col">
        <div class="section-title">New Releases</div>
        {% if releases %}
        <div class="releases-grid">
            {% for r in releases %}
            <a href="{{ r.url }}" target="_blank" class="release-card">
                {% if r.image %}
                <img src="{{ r.image }}" alt="{{ r.name }}">
                {% else %}
                <div class="no-img">♪</div>
                {% endif %}
                <div class="release-info">
                    <div class="release-name">{{ r.name }}</div>
                    <div class="release-artist">{{ r.artist }}</div>
                    <div class="release-type">{{ r.type }} · {{ r.date }}</div>
                </div>
            </a>
            {% endfor %}
        </div>
        {% else %}
        <p style="font-size:0.8em;color:#aaa;">Could not load new releases — Spotify may be unavailable.</p>
        {% endif %}
    </div>
</div>

<script>
    function toggleDark() {
        var isDark = document.body.classList.toggle('dark');
        localStorage.setItem('dark', isDark ? '1' : '0');
        document.getElementById('dark-btn').textContent = isDark ? '☀' : '◑';
    }
    (function() {
        if (localStorage.getItem('dark') === '1') {
            document.body.classList.add('dark');
            var btn = document.getElementById('dark-btn');
            if (btn) btn.textContent = '☀';
        }
    })();
</script>
</body>
</html>
"""

@app.route("/news")
def news():
    data = get_news_data()
    return render_template_string(NEWS_TEMPLATE, articles=data["articles"], releases=data["releases"])

@app.route("/news/refresh", methods=["POST"])
def news_refresh():
    _news_cache["data"] = None
    _news_cache["fetched_at"] = 0
    return redirect("/news")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("RAILWAY_ENVIRONMENT") is None  # debug off on Railway
    app.run(host="0.0.0.0", port=port, debug=debug)
from flask import Flask, render_template_string, request, redirect, url_for
import psycopg2
import os
import json
from dotenv import load_dotenv
from pipeline import run_pipeline

load_dotenv()

app = Flask(__name__)

def get_db_connection():
    return psycopg2.connect(dbname="music_insights", user=os.getenv("USER"))

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Music Insight Pipeline</title>
    <link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: 'Inter', sans-serif;
            background: #f5f5f3;
            color: #111;
            min-height: 100vh;
        }

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
            color: #888;
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
        }
        .search-form {
            display: flex;
            width: 100%;
            max-width: 440px;
        }
        .search-form input {
            flex: 1;
            padding: 11px 20px;
            background: #f5f5f3;
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

        /* ── TWO-COLUMN GRID ── */
        .main-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
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
            max-width: 160px;
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
    </style>
</head>
<body>

    <!-- TICKER -->
    <div class="ticker">
        <div class="ticker-inner">
            <span>Music Insight Pipeline — Live</span>
            <span>Last.fm · Claude AI · PostgreSQL</span>
            <span>Manchester, UK — 2026</span>
            <span>Automated Music Intelligence</span>
            <span>Music Insight Pipeline — Live</span>
            <span>Last.fm · Claude AI · PostgreSQL</span>
            <span>Manchester, UK — 2026</span>
            <span>Automated Music Intelligence</span>
        </div>
    </div>

    <!-- TOPBAR -->
    <div class="topbar">
        <span class="topbar-brand">MIP</span>
        <div class="topbar-right">
            <span class="topbar-link">Last.fm + Claude AI + PostgreSQL</span>
            <span class="live-pill"><span class="dot"></span>Live</span>
        </div>
    </div>

    <!-- HERO -->
    <div class="hero">
        <div class="hero-eyebrow">Music Intelligence · Manchester</div>
        <h1>Music Insight Pipeline</h1>
        <p class="hero-sub">Search any artist — Claude analyses their top tracks in seconds</p>
    </div>

    <!-- SEARCH -->
    <form method="POST" action="/search">
        <div class="search-wrap">
            <div class="search-form">
                <input type="text" name="artist" placeholder="Search an artist..." required />
                <button type="submit">Analyse</button>
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
            <div class="panel-title">Latest Claude Insights</div>
            {% for row in latest_insights %}
            {% set clean = row.insight | replace('##', '') | replace('**', '') | replace('# ', '') %}
            <div class="insight-card">
                <div class="insight-meta">
                    <span class="insight-artist">{{ row.artist }}</span>
                    <span class="insight-time">{{ row.searched_at.strftime('%d %b %Y  %H:%M') }}</span>
                </div>
                <div class="track-tags">
                    {% for t in row.top_tracks %}
                    <span class="track-tag">{{ t }}</span>
                    {% endfor %}
                </div>
                <div class="insight-body" id="body-{{ loop.index }}">
                    <span class="insight-preview">{{ clean[:200] }}...</span>
                    <span class="insight-full">{{ clean }}</span>
                </div>
                <button class="insight-toggle" onclick="toggleInsight({{ loop.index }}, this)">Read more</button>
            </div>
            {% endfor %}
        </div>

    </div>

    <!-- FOOTER -->
    <div class="footer">
        <span class="footer-brand">Music Insight Pipeline</span>
        <span class="footer-sub">Built by Dimos Dimitrios Papageorgiou · Manchester · 2026</span>
    </div>

    <script>
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

    latest_insights = [Row(r) for r in cur.fetchall()]
    cur.close()
    conn.close()

    return total_searches, unique_artists, searches_today, artist_plays, latest_insights

@app.route("/")
def dashboard():
    message = request.args.get("message")
    error = request.args.get("error")
    total_searches, unique_artists, searches_today, artist_plays, latest_insights = get_dashboard_data()
    return render_template_string(HTML_TEMPLATE,
        total_searches=total_searches,
        unique_artists=unique_artists,
        searches_today=searches_today,
        artist_plays=artist_plays,
        latest_insights=latest_insights,
        message=message,
        error=error
    )

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

if __name__ == "__main__":
    app.run(debug=True, port=5000)
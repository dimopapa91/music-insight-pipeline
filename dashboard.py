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
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: Arial, sans-serif; background: #0f0f0f; color: #fff; padding: 30px; }
        h1 { color: #1db954; margin-bottom: 5px; font-size: 2em; }
        .subtitle { color: #888; margin-bottom: 30px; }
        .search-box { display: flex; gap: 10px; margin-bottom: 30px; }
        .search-box input { flex: 1; padding: 12px 16px; border-radius: 8px; border: 1px solid #333; background: #1a1a1a; color: #fff; font-size: 1em; }
        .search-box input:focus { outline: none; border-color: #1db954; }
        .search-box button { padding: 12px 24px; background: #1db954; color: #000; border: none; border-radius: 8px; font-size: 1em; font-weight: bold; cursor: pointer; }
        .search-box button:hover { background: #1ed760; }
        .alert { background: #1a1a1a; border-left: 3px solid #1db954; padding: 12px 16px; border-radius: 8px; margin-bottom: 20px; color: #1db954; }
        .error { border-left-color: #e74c3c; color: #e74c3c; }
        .stats { display: flex; gap: 20px; margin-bottom: 30px; }
        .stat-card { background: #1a1a1a; border-radius: 10px; padding: 20px; flex: 1; text-align: center; border: 1px solid #333; }
        .stat-card h2 { font-size: 2.5em; color: #1db954; }
        .stat-card p { color: #888; margin-top: 5px; }
        .section { background: #1a1a1a; border-radius: 10px; padding: 20px; margin-bottom: 20px; border: 1px solid #333; }
        .section h3 { color: #1db954; margin-bottom: 15px; font-size: 1.2em; }
        .insight-card { background: #111; border-radius: 8px; padding: 15px; margin-bottom: 15px; border-left: 3px solid #1db954; }
        .insight-card h4 { color: #1db954; margin-bottom: 8px; }
        .insight-card p { color: #ccc; font-size: 0.9em; line-height: 1.6; }
        .timestamp { color: #555; font-size: 0.8em; margin-top: 8px; }
        .bar-container { display: flex; align-items: center; gap: 10px; margin: 8px 0; }
        .bar { height: 20px; background: #1db954; border-radius: 4px; min-width: 4px; }
        .bar-label { color: #888; font-size: 0.85em; width: 140px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .bar-value { color: #fff; font-size: 0.85em; }
        .loading { color: #888; font-style: italic; }
    </style>
</head>
<body>
    <h1>🎵 Music Insight Pipeline</h1>
    <p class="subtitle">Real-time dashboard — powered by Last.fm + Claude AI + PostgreSQL</p>

    <div class="search-box">
        <form method="POST" action="/search" style="display:flex; gap:10px; flex:1;">
            <input type="text" name="artist" placeholder="Search any artist e.g. Frank Ocean..." required />
            <button type="submit">Analyse 🔍</button>
        </form>
    </div>

    {% if message %}
    <div class="alert {{ 'error' if error else '' }}">{{ message }}</div>
    {% endif %}

    <div class="stats">
        <div class="stat-card">
            <h2>{{ total_searches }}</h2>
            <p>Total Searches</p>
        </div>
        <div class="stat-card">
            <h2>{{ unique_artists }}</h2>
            <p>Unique Artists</p>
        </div>
        <div class="stat-card">
            <h2>{{ searches_today }}</h2>
            <p>Searches Today</p>
        </div>
    </div>

    <div class="section">
        <h3>📊 Average Plays by Artist</h3>
        {% for artist, avg, max_avg in artist_plays %}
        <div class="bar-container">
            <span class="bar-label">{{ artist }}</span>
            <div class="bar" style="width: {{ [((avg / max_avg) * 300)|int, 4]|max }}px"></div>
            <span class="bar-value">{{ "{:,}".format(avg) }} avg plays</span>
        </div>
        {% endfor %}
    </div>

    <div class="section">
        <h3>🤖 Latest Claude Insights</h3>
        {% for row in latest_insights %}
        <div class="insight-card">
            <h4>{{ row.artist }}</h4>
            <p>{{ row.insight[:400] }}...</p>
            <p class="timestamp">{{ row.searched_at.strftime('%d %b %Y %H:%M') }}</p>
        </div>
        {% endfor %}
    </div>
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
        SELECT artist_name, claude_insight, searched_at
        FROM searches ORDER BY searched_at DESC LIMIT 5
    """)

    class Row:
        def __init__(self, r):
            self.artist = r[0]
            self.insight = r[1]
            self.searched_at = r[2]

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
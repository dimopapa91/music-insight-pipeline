import os
import json
import requests
import psycopg2
import anthropic
from dotenv import load_dotenv

load_dotenv()

LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")
BASE_URL = "http://ws.audioscrobbler.com/2.0/"
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

def get_db_connection():
    return psycopg2.connect(dbname="music_insights", user=os.getenv("USER"))

def get_top_tracks(artist_name):
    """Fetch top tracks for a given artist from Last.fm"""
    params = {
        "method": "artist.gettoptracks",
        "artist": artist_name,
        "api_key": LASTFM_API_KEY,
        "format": "json",
        "limit": 5
    }
    response = requests.get(BASE_URL, params=params)
    data = response.json()
    return data["toptracks"]["track"]

def analyse_with_claude(artist_name, tracks):
    """Send track data to Claude for analysis"""
    track_list = "\n".join([
        f"{i+1}. {t['name']} — {t['playcount']} plays"
        for i, t in enumerate(tracks)
    ])
    prompt = f"""You are a music analyst. Here are the top 5 most played tracks by {artist_name} on Last.fm:

{track_list}

Please give me:
1. A brief analysis of what these tracks reveal about {artist_name}'s appeal
2. What production or songwriting patterns might explain their popularity
3. One recommendation for a similar artist someone might enjoy
Keep it concise and insightful."""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text

def save_to_db(artist_name, tracks, insight):
    """Save search results to PostgreSQL"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO searches (artist_name, top_tracks, claude_insight) VALUES (%s, %s, %s)",
        (artist_name, json.dumps(tracks), insight)
    )
    conn.commit()
    cur.close()
    conn.close()

def run_pipeline(artist_name):
    """Run the full pipeline for a given artist"""
    print(f"[Pipeline] Fetching data for {artist_name}...")
    tracks = get_top_tracks(artist_name)
    print(f"[Pipeline] Analysing with Claude...")
    insight = analyse_with_claude(artist_name, tracks)
    save_to_db(artist_name, tracks, insight)
    print(f"[Pipeline] Done — {artist_name} saved to database!")
    return insight
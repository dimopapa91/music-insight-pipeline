import os
import json
import logging
import requests
import psycopg2
import anthropic
from dotenv import load_dotenv

load_dotenv()

LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")
BASE_URL = "http://ws.audioscrobbler.com/2.0/"
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("pipeline.log"),
        logging.StreamHandler()
    ]
)

def get_db_connection():
    try:
        conn = psycopg2.connect(dbname="music_insights", user=os.getenv("USER"))
        return conn
    except psycopg2.OperationalError as e:
        logging.error(f"Database connection failed: {e}")
        raise

def get_top_tracks(artist_name):
    """Fetch top tracks for a given artist from Last.fm"""
    try:
        params = {
            "method": "artist.gettoptracks",
            "artist": artist_name,
            "api_key": LASTFM_API_KEY,
            "format": "json",
            "limit": 5
        }
        response = requests.get(BASE_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if "error" in data:
            raise ValueError(f"Last.fm API error: {data['message']}")

        if "toptracks" not in data or "track" not in data["toptracks"]:
            raise ValueError(f"No track data found for artist: {artist_name}")

        tracks = data["toptracks"]["track"]
        logging.info(f"Fetched {len(tracks)} tracks for {artist_name}")
        return tracks

    except requests.exceptions.Timeout:
        logging.error(f"Last.fm API timed out for {artist_name}")
        raise
    except requests.exceptions.RequestException as e:
        logging.error(f"Last.fm API request failed: {e}")
        raise

def analyse_with_claude(artist_name, tracks):
    """Send track data to Claude for analysis"""
    try:
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
        logging.info(f"Claude analysis completed for {artist_name}")
        return message.content[0].text

    except anthropic.APIError as e:
        logging.error(f"Claude API error for {artist_name}: {e}")
        raise
    except Exception as e:
        logging.error(f"Unexpected error during Claude analysis: {e}")
        raise

def save_to_db(artist_name, tracks, insight):
    """Save search results to PostgreSQL"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO searches (artist_name, top_tracks, claude_insight) VALUES (%s, %s, %s)",
            (artist_name, json.dumps(tracks), insight)
        )
        conn.commit()
        cur.close()
        logging.info(f"Saved {artist_name} to database")
    except psycopg2.Error as e:
        logging.error(f"Database error saving {artist_name}: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

def run_pipeline(artist_name):
    """Run the full pipeline for a given artist"""
    logging.info(f"Pipeline started for: {artist_name}")
    try:
        tracks = get_top_tracks(artist_name)
        insight = analyse_with_claude(artist_name, tracks)
        save_to_db(artist_name, tracks, insight)
        logging.info(f"Pipeline completed successfully for: {artist_name}")
        return insight
    except Exception as e:
        logging.error(f"Pipeline failed for {artist_name}: {e}")
        raise
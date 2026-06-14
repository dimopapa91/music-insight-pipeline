from dotenv import load_dotenv
import os
import requests
import anthropic
import psycopg2
import json

load_dotenv()

LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")
BASE_URL = "http://ws.audioscrobbler.com/2.0/"

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# Database connection
conn = psycopg2.connect(dbname="music_insights", user=os.getenv("USER"))
cur = conn.cursor()

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
    cur.execute(
        "INSERT INTO searches (artist_name, top_tracks, claude_insight) VALUES (%s, %s, %s)",
        (artist_name, json.dumps(tracks), insight)
    )
    conn.commit()
    print("✅ Saved to database!")

def show_history():
    """Show all previous searches"""
    cur.execute("SELECT artist_name, searched_at FROM searches ORDER BY searched_at DESC")
    rows = cur.fetchall()
    if rows:
        print("\n📚 Previous searches:")
        for row in rows:
            print(f"  - {row[0]} ({row[1].strftime('%d %b %Y %H:%M')})")
    else:
        print("\nNo previous searches yet.")

# Run the pipeline
show_history()
artist = input("\nEnter an artist name: ")
print(f"\nFetching data for {artist}...")
tracks = get_top_tracks(artist)
print("Analysing with Claude...\n")
insight = analyse_with_claude(artist, tracks)
print(insight)
save_to_db(artist, tracks, insight)
show_history()
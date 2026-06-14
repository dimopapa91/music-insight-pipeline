import psycopg2
import os
import json
import requests
from dotenv import load_dotenv
import anthropic

load_dotenv()

LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")
BASE_URL = "http://ws.audioscrobbler.com/2.0/"
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

def get_db_connection():
    return psycopg2.connect(dbname="music_insights", user=os.getenv("USER"))

def get_artist_tags(artist_name):
    """Fetch genre tags for an artist from Last.fm"""
    params = {
        "method": "artist.getinfo",
        "artist": artist_name,
        "api_key": LASTFM_API_KEY,
        "format": "json"
    }
    response = requests.get(BASE_URL, params=params, timeout=10)
    data = response.json()
    if "error" in data or "artist" not in data:
        return []
    tags = data["artist"].get("tags", {}).get("tag", [])
    return [t["name"].lower() for t in tags[:5]]

def analyse_genre_trends():
    """Fetch all artists from DB and analyse genre trends"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT artist_name FROM searches")
    artists = [row[0] for row in cur.fetchall()]
    cur.close()
    conn.close()

    print(f"\n🎵 Fetching genre tags for {len(artists)} artists...")

    genre_count = {}
    artist_genres = {}

    for artist in artists:
        tags = get_artist_tags(artist)
        artist_genres[artist] = tags
        for tag in tags:
            genre_count[tag] = genre_count.get(tag, 0) + 1
        print(f"  {artist}: {', '.join(tags) if tags else 'no tags found'}")

    # Sort genres by frequency
    sorted_genres = sorted(genre_count.items(), key=lambda x: x[1], reverse=True)

    print("\n📊 Genre Trends Across Your Pipeline:")
    print("─" * 40)
    for genre, count in sorted_genres[:10]:
        bar = "█" * (count * 5)
        print(f"  {genre:<20} {bar} ({count})")

    # Ask Claude to analyse the trends
    genre_summary = "\n".join([f"- {g}: {c} artist(s)" for g, c in sorted_genres[:10]])
    artist_summary = "\n".join([f"- {a}: {', '.join(g) if g else 'unknown'}" for a, g in artist_genres.items()])

    prompt = f"""You are a music data analyst. Here is genre data collected from a music pipeline:

Artists analysed:
{artist_summary}

Top genres found:
{genre_summary}

Please provide:
1. What do these genre trends reveal about the music being analysed?
2. Are there any interesting genre overlaps or patterns?
3. What does this suggest about the listener's taste profile?
Keep it concise and insightful."""

    print("\n🤖 Claude's Genre Trend Analysis:")
    print("─" * 40)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    print(message.content[0].text)

if __name__ == "__main__":
    analyse_genre_trends()
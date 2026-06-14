import os
import json
import requests
import anthropic
from dotenv import load_dotenv

load_dotenv()

LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")
BASE_URL = "http://ws.audioscrobbler.com/2.0/"
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

def get_top_tracks(artist_name):
    """Fetch top tracks for a given artist from Last.fm"""
    params = {
        "method": "artist.gettoptracks",
        "artist": artist_name,
        "api_key": LASTFM_API_KEY,
        "format": "json",
        "limit": 5
    }
    response = requests.get(BASE_URL, params=params, timeout=10)
    data = response.json()
    if "error" in data:
        raise ValueError(f"Artist not found: {artist_name}")
    return data["toptracks"]["track"]

def get_artist_info(artist_name):
    """Fetch artist bio and tags from Last.fm"""
    params = {
        "method": "artist.getinfo",
        "artist": artist_name,
        "api_key": LASTFM_API_KEY,
        "format": "json"
    }
    response = requests.get(BASE_URL, params=params, timeout=10)
    data = response.json()
    if "error" in data:
        return None
    artist = data["artist"]
    tags = [t["name"] for t in artist.get("tags", {}).get("tag", [])[:5]]
    listeners = int(artist["stats"]["listeners"])
    playcount = int(artist["stats"]["playcount"])
    return {"tags": tags, "listeners": listeners, "playcount": playcount}

def format_number(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)

def compare_with_claude(artist1, artist2, tracks1, tracks2, info1, info2):
    """Ask Claude to compare two artists head to head"""
    def track_list(tracks):
        return "\n".join([f"{i+1}. {t['name']} — {int(t['playcount']):,} plays"
                         for i, t in enumerate(tracks)])

    prompt = f"""You are a music analyst comparing two artists head to head.

ARTIST 1: {artist1}
Listeners: {format_number(info1['listeners'])} | Total plays: {format_number(info1['playcount'])}
Tags: {', '.join(info1['tags'])}
Top tracks:
{track_list(tracks1)}

ARTIST 2: {artist2}
Listeners: {format_number(info2['listeners'])} | Total plays: {format_number(info2['playcount'])}
Tags: {', '.join(info2['tags'])}
Top tracks:
{track_list(tracks2)}

Please provide:
1. **Similarities** — what do these artists share in terms of sound, appeal or audience?
2. **Key differences** — what sets them apart musically and commercially?
3. **Audience crossover** — would fans of one enjoy the other? Why?
4. **Production comparison** — how do their production styles differ?
5. **Verdict** — which artist has broader commercial appeal and why?

Be specific, insightful and concise."""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text

def run_comparison():
    print("\n" + "=" * 50)
    print("       🎵 ARTIST COMPARISON MODE 🎵")
    print("=" * 50)

    artist1 = input("\nEnter first artist: ").strip()
    artist2 = input("Enter second artist: ").strip()

    print(f"\nFetching data for {artist1} and {artist2}...")

    try:
        tracks1 = get_top_tracks(artist1)
        tracks2 = get_top_tracks(artist2)
        info1 = get_artist_info(artist1)
        info2 = get_artist_info(artist2)

        if not info1 or not info2:
            print("Could not fetch artist info. Check the artist names and try again.")
            return

        # Print stats side by side
        print(f"\n{'─' * 50}")
        print(f"{'ARTIST':<20} {'LISTENERS':>12} {'TOTAL PLAYS':>14}")
        print(f"{'─' * 50}")
        print(f"{artist1:<20} {format_number(info1['listeners']):>12} {format_number(info1['playcount']):>14}")
        print(f"{artist2:<20} {format_number(info2['listeners']):>12} {format_number(info2['playcount']):>14}")
        print(f"{'─' * 50}")

        print(f"\n🏷️  {artist1} tags: {', '.join(info1['tags'])}")
        print(f"🏷️  {artist2} tags: {', '.join(info2['tags'])}")

        print(f"\n⏳ Asking Claude to compare them...\n")
        comparison = compare_with_claude(artist1, artist2, tracks1, tracks2, info1, info2)
        print(comparison)
        print("\n" + "=" * 50)

    except ValueError as e:
        print(f"❌ Error: {e}")
    except Exception as e:
        print(f"❌ Something went wrong: {e}")

if __name__ == "__main__":
    run_comparison()
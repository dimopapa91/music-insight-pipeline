import os
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from dotenv import load_dotenv

load_dotenv()

# Authenticate with Spotify
sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=os.getenv("SPOTIFY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIFY_CLIENT_SECRET")
))

def get_spotify_data(artist_name):
    """Fetch artist data from Spotify"""
    # Search for the artist
    results = sp.search(q=artist_name, type="artist", limit=1)
    artists = results["artists"]["items"]

    if not artists:
        raise ValueError(f"Artist not found on Spotify: {artist_name}")

    artist = artists[0]

    # Get top tracks
    top_tracks = sp.artist_top_tracks(artist["id"], country="GB")["tracks"][:5]

    return {
        "name": artist["name"],
        "followers": artist["followers"]["total"],
        "popularity": artist["popularity"],
        "genres": artist["genres"][:5],
        "top_tracks": [
            {
                "name": t["name"],
                "popularity": t["popularity"],
                "duration_ms": t["duration_ms"],
                "preview_url": t["preview_url"]
            }
            for t in top_tracks
        ]
    }

def format_spotify_summary(data):
    """Format Spotify data into a readable summary"""
    tracks = "\n".join([
        f"{i+1}. {t['name']} (popularity: {t['popularity']}/100)"
        for i, t in enumerate(data["top_tracks"])
    ])

    return f"""
Spotify Data for {data['name']}:
- Followers: {data['followers']:,}
- Popularity Score: {data['popularity']}/100
- Genres: {', '.join(data['genres']) if data['genres'] else 'N/A'}
- Top Tracks:
{tracks}
"""

if __name__ == "__main__":
    artist = input("Enter artist name: ")
    try:
        data = get_spotify_data(artist)
        print(format_spotify_summary(data))
    except ValueError as e:
        print(f"Error: {e}")
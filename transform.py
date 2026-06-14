import psycopg2
import os
import json
from dotenv import load_dotenv
from collections import Counter

load_dotenv()

def get_db_connection():
    return psycopg2.connect(dbname="music_insights", user=os.getenv("USER"))

def most_searched_artists():
    """Which artists have been analysed most?"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT artist_name, COUNT(*) as search_count
        FROM searches
        GROUP BY artist_name
        ORDER BY search_count DESC
        LIMIT 10
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    print("\n🎵 Most Analysed Artists:")
    print("-" * 40)
    for row in rows:
        print(f"  {row[0]}: {row[1]} search(es)")

def average_plays_per_artist():
    """Average play count of top tracks per artist"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT artist_name, top_tracks FROM searches")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    artist_avg = {}
    for artist_name, top_tracks in rows:
        tracks = top_tracks if isinstance(top_tracks, list) else json.loads(top_tracks)
        plays = [int(t["playcount"]) for t in tracks]
        avg = sum(plays) // len(plays)
        if artist_name not in artist_avg:
            artist_avg[artist_name] = []
        artist_avg[artist_name].append(avg)

    # Average across all searches for that artist
    final = {k: sum(v) // len(v) for k, v in artist_avg.items()}
    sorted_artists = sorted(final.items(), key=lambda x: x[1], reverse=True)

    print("\n📊 Average Track Plays by Artist:")
    print("-" * 40)
    for artist, avg in sorted_artists:
        print(f"  {artist}: {avg:,} avg plays")

def searches_over_time():
    """How many searches have been run per day?"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT DATE(searched_at) as date, COUNT(*) as count
        FROM searches
        GROUP BY DATE(searched_at)
        ORDER BY date DESC
        LIMIT 7
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    print("\n📅 Searches Per Day (last 7 days):")
    print("-" * 40)
    for row in rows:
        bar = "█" * row[1]
        print(f"  {row[0]}: {bar} ({row[1]})")

def total_stats():
    """Overall database stats"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM searches")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT artist_name) FROM searches")
    unique = cur.fetchone()[0]
    cur.close()
    conn.close()

    print("\n📈 Pipeline Stats:")
    print("-" * 40)
    print(f"  Total searches run: {total}")
    print(f"  Unique artists analysed: {unique}")

# Run all transformations
print("=" * 40)
print("   MUSIC INSIGHT PIPELINE — REPORT")
print("=" * 40)
total_stats()
most_searched_artists()
average_plays_per_artist()
searches_over_time()
print("\n" + "=" * 40)
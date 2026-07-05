"""Pytest configuration — runs before test collection.

Sets dummy API credentials so modules import without real secrets, and
ensures DATABASE_URL is unset so no test can touch a real database.
"""

import os

os.environ.setdefault("LASTFM_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("SPOTIFY_CLIENT_ID", "test")
os.environ.setdefault("SPOTIFY_CLIENT_SECRET", "test")
os.environ.pop("DATABASE_URL", None)

"""Pytest configuration — runs before test collection.

Sets dummy API credentials so modules import without real secrets, and
ensures DATABASE_URL can never resolve to a real database.

IMPORTANT: `dashboard.py` calls `load_dotenv()` at import time, and
`load_dotenv()`'s default `override=False` only refuses to touch a variable
that's already *present* in os.environ — merely `pop()`-ing DATABASE_URL
first is NOT enough, because the moment something imports `dashboard`
during collection, load_dotenv() sees it "unset" and happily repopulates it
from the real `.env` file (which points at the production Railway
database). That happened for real during analytics-feature development —
see git history / PR notes. So DATABASE_URL is pinned here to an obviously
bogus, guaranteed-unreachable value instead of merely removed: since it's
then *already set*, load_dotenv() leaves it alone, and any code that tries
to connect gets a clean, fast connection failure instead of silently
succeeding against production.
"""

import contextlib
import os

import pytest

os.environ.setdefault("LASTFM_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("SPOTIFY_CLIENT_ID", "test")
os.environ.setdefault("SPOTIFY_CLIENT_SECRET", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ["DATABASE_URL"] = "postgresql://test:test@127.0.0.1:1/pytest_must_not_reach_a_real_db"


@pytest.fixture(autouse=True)
def _no_real_analytics_writes(monkeypatch):
    """The analytics `after_request` hook (see analytics.py) runs on every
    Flask test-client request in the whole suite, not just tests that are
    actually about analytics. Give it a harmless no-op db_cursor by default
    so those requests don't fall back to a real local Postgres connection —
    consistent with the "no test can touch a real database" contract above.
    Tests that specifically exercise analytics recording/queries override
    this themselves with their own monkeypatch.setattr(analytics, ...).
    """
    class _NoopCursor:
        def execute(self, *a, **k):
            pass

        def fetchone(self):
            return None

        def fetchall(self):
            return []

    @contextlib.contextmanager
    def _noop_db_cursor(commit=False):
        yield _NoopCursor()

    try:
        import analytics
        monkeypatch.setattr(analytics, "db_cursor", _noop_db_cursor)
    except ImportError:
        pass

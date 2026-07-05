"""Shared database helpers for the Waveline pipeline.

Centralises PostgreSQL connection handling so every module (dashboard,
pipeline, analytics scripts) uses the same logic:

* On Railway/production, connect via the DATABASE_URL environment variable.
* Locally, fall back to the `music_insights` database for the current OS user.

The ``db_cursor`` context manager guarantees the connection is always closed —
even if the query raises — which prevents connection leaks under load.
"""

import os
import logging
from contextlib import contextmanager

import psycopg2

logger = logging.getLogger(__name__)


def get_db_connection():
    """Return a new PostgreSQL connection.

    Uses DATABASE_URL when present (Railway/production), otherwise falls back
    to a local `music_insights` database for development.
    """
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return psycopg2.connect(database_url)
    return psycopg2.connect(dbname="music_insights", user=os.getenv("USER"))


@contextmanager
def db_cursor(commit=False):
    """Yield a cursor and always clean up the connection.

    Usage::

        with db_cursor() as cur:
            cur.execute("SELECT ...")
            rows = cur.fetchall()

    Pass ``commit=True`` for writes. On any exception the transaction is rolled
    back and re-raised; the connection is closed in all cases.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()

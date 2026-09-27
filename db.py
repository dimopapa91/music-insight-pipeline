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
import threading
from contextlib import contextmanager

import psycopg2
import psycopg2.pool

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


# ── connection pool ─────────────────────────────────────────────────
#
# Every db_cursor() used to open a brand-new Postgres connection (TCP + TLS +
# auth) and close it again, several times per page. With one gunicorn
# process and 8 threads, a small thread-safe pool reuses connections
# instead. Enabled when DATABASE_URL is set and DB_POOL isn't "0" (the test
# suite sets DB_POOL=0 so its fake connections keep working). Connections
# that error out are discarded, never returned to the pool.
_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))
_pool = None
_pool_lock = threading.Lock()


def _pool_enabled():
    return bool(os.getenv("DATABASE_URL")) and os.getenv("DB_POOL", "1") != "0"


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    0, _POOL_MAX, os.getenv("DATABASE_URL"),
                    keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
                )
    return _pool


def _acquire():
    """(conn, pool) — pool is None when the connection isn't pooled."""
    if not _pool_enabled():
        return get_db_connection(), None
    pool = _get_pool()
    try:
        conn = pool.getconn()
    except psycopg2.pool.PoolError:
        # Pool exhausted: fall back to a one-off connection rather than fail.
        return get_db_connection(), None
    if conn.closed:
        pool.putconn(conn, close=True)
        conn = pool.getconn()
    return conn, pool


@contextmanager
def db_cursor(commit=False):
    """Yield a cursor and always clean up the connection.

    Usage::

        with db_cursor() as cur:
            cur.execute("SELECT ...")
            rows = cur.fetchall()

    Pass ``commit=True`` for writes. On any exception the transaction is rolled
    back and re-raised. Unpooled connections are always closed; pooled ones
    go back to the pool with no open transaction, or are discarded if broken.
    """
    conn, pool = _acquire()
    cur = conn.cursor()
    broken = False
    try:
        yield cur
        if commit:
            conn.commit()
        elif pool is not None:
            conn.rollback()   # end the implicit read transaction before reuse
    except Exception as e:
        broken = isinstance(e, (psycopg2.OperationalError, psycopg2.InterfaceError))
        try:
            conn.rollback()
        except Exception:
            broken = True
        raise
    finally:
        try:
            cur.close()
        except Exception:
            pass
        if pool is None:
            conn.close()
        else:
            try:
                pool.putconn(conn, close=broken or bool(conn.closed))
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass

"""User model and account persistence for Waveline.

Uses the shared ``db`` helpers (raw psycopg2, no ORM) to stay consistent with
the rest of the project. ``init_db`` creates every application table with
``IF NOT EXISTS`` so it is safe to run on every startup.
"""

import logging

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from db import db_cursor

logger = logging.getLogger(__name__)

# All statements are idempotent so init_db() can run on every boot/worker.
SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id            SERIAL PRIMARY KEY,
        username      VARCHAR(30)  UNIQUE NOT NULL,
        email         VARCHAR(255) UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        bio           TEXT DEFAULT '',
        created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # Link searches to the user who ran them (existing rows stay NULL = anonymous)
    "ALTER TABLE searches ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id)",
    # Richer profile fields (added later — safe on existing users)
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS location VARCHAR(120) DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS website  VARCHAR(255) DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS genres   VARCHAR(255) DEFAULT ''",
    # Phase 2 (feed) tables — created now so the feed needs no further migration
    """
    CREATE TABLE IF NOT EXISTS posts (
        id         SERIAL PRIMARY KEY,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        body       TEXT NOT NULL,
        artist     VARCHAR(255),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS follows (
        follower_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        followee_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (follower_id, followee_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS likes (
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        post_id    INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, post_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS comments (
        id         SERIAL PRIMARY KEY,
        post_id    INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        body       TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS notifications (
        id         SERIAL PRIMARY KEY,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,  -- recipient
        actor_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,  -- who did it
        type       VARCHAR(20) NOT NULL,                                     -- like | comment | follow
        post_id    INTEGER REFERENCES posts(id) ON DELETE CASCADE,
        is_read    BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications (user_id, is_read)",
    # Self-hosted analytics — one row per recorded HTML page view. No raw IP
    # is ever stored (see analytics.py); visitor_hash is a daily-rotating hash.
    """
    CREATE TABLE IF NOT EXISTS analytics_events (
        id           SERIAL PRIMARY KEY,
        path         TEXT NOT NULL,
        referrer     TEXT,
        country      CHAR(2),
        user_id      INTEGER REFERENCES users(id),
        visitor_hash TEXT NOT NULL,
        created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_analytics_created_at ON analytics_events (created_at)",
    "CREATE INDEX IF NOT EXISTS idx_analytics_country ON analytics_events (country)",
    "CREATE INDEX IF NOT EXISTS idx_analytics_path ON analytics_events (path)",
]


def init_db():
    """Ensure all application tables exist. Safe to call repeatedly."""
    try:
        with db_cursor(commit=True) as cur:
            for statement in SCHEMA:
                cur.execute(statement)
        logger.info("Database schema ensured (users, posts, follows, likes).")
    except Exception as e:  # never let a transient DB hiccup crash startup
        logger.error(f"init_db failed: {e}")


class User(UserMixin):
    def __init__(self, id, username, email, password_hash, bio="",
                 location="", website="", genres="", created_at=None):
        self.id = id
        self.username = username
        self.email = email
        self.password_hash = password_hash
        self.bio = bio or ""
        self.location = location or ""
        self.website = website or ""
        self.genres = genres or ""
        self.created_at = created_at

    @property
    def genre_list(self):
        return [g.strip() for g in (self.genres or "").split(",") if g.strip()]

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def update_profile(self, bio, location="", website="", genres=""):
        with db_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE users SET bio = %s, location = %s, website = %s, genres = %s WHERE id = %s",
                (bio, location, website, genres, self.id),
            )
        self.bio, self.location, self.website, self.genres = bio, location, website, genres

    # Backwards-compatible alias
    def update_bio(self, bio):
        self.update_profile(bio, self.location, self.website, self.genres)

    _COLUMNS = "id, username, email, password_hash, bio, location, website, genres, created_at"

    @classmethod
    def _from_row(cls, row):
        if not row:
            return None
        return cls(id=row[0], username=row[1], email=row[2], password_hash=row[3],
                   bio=row[4], location=row[5], website=row[6], genres=row[7],
                   created_at=row[8])

    @classmethod
    def get(cls, user_id):
        with db_cursor() as cur:
            cur.execute(f"SELECT {cls._COLUMNS} FROM users WHERE id = %s", (user_id,))
            return cls._from_row(cur.fetchone())

    @classmethod
    def get_by_username(cls, username):
        with db_cursor() as cur:
            cur.execute(f"SELECT {cls._COLUMNS} FROM users WHERE LOWER(username) = LOWER(%s)", (username,))
            return cls._from_row(cur.fetchone())

    @classmethod
    def get_by_email(cls, email):
        with db_cursor() as cur:
            cur.execute(f"SELECT {cls._COLUMNS} FROM users WHERE LOWER(email) = LOWER(%s)", (email,))
            return cls._from_row(cur.fetchone())

    @classmethod
    def create(cls, username, email, password):
        pw_hash = generate_password_hash(password)
        with db_cursor(commit=True) as cur:
            cur.execute(
                f"INSERT INTO users (username, email, password_hash) "
                f"VALUES (%s, %s, %s) RETURNING {cls._COLUMNS}",
                (username, email, pw_hash),
            )
            return cls._from_row(cur.fetchone())

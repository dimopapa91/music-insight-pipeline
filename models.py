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
    def __init__(self, id, username, email, password_hash, bio="", created_at=None):
        self.id = id
        self.username = username
        self.email = email
        self.password_hash = password_hash
        self.bio = bio or ""
        self.created_at = created_at

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def update_bio(self, bio):
        with db_cursor(commit=True) as cur:
            cur.execute("UPDATE users SET bio = %s WHERE id = %s", (bio, self.id))
        self.bio = bio

    _COLUMNS = "id, username, email, password_hash, bio, created_at"

    @classmethod
    def _from_row(cls, row):
        if not row:
            return None
        return cls(id=row[0], username=row[1], email=row[2],
                   password_hash=row[3], bio=row[4], created_at=row[5])

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

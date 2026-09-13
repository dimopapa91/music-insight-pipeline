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
    # Phase 3: Cloudinary-hosted image URLs only — no binary image data is
    # ever stored in Postgres. TEXT (not VARCHAR) since a Cloudinary
    # secure_url can run long. Existing users default to '' (falsy), so
    # they keep seeing the generated monogram / CSS cover fallback.
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_image_url TEXT DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS cover_image_url   TEXT DEFAULT ''",
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
    # Phase 6: private one-to-one messaging. A conversation is the canonical,
    # order-independent pair of two users (user1_id < user2_id enforced by
    # the CHECK below, so the app always sorts the pair before any lookup —
    # see messaging._canonical_pair) with at most one row per pair, courtesy
    # of the UNIQUE constraint. That UNIQUE index also serves participant
    # lookups, so no separate index is needed for that.
    """
    CREATE TABLE IF NOT EXISTS conversations (
        id         SERIAL PRIMARY KEY,
        user1_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        user2_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT conversations_pair_unique UNIQUE (user1_id, user2_id),
        CONSTRAINT conversations_canonical_order CHECK (user1_id < user2_id)
    )
    """,
    # is_read/recipient_id are denormalised onto the message itself (rather
    # than derived from the conversation) so unread counts and "mark this
    # conversation read" updates are single, simple, indexed statements.
    """
    CREATE TABLE IF NOT EXISTS direct_messages (
        id              SERIAL PRIMARY KEY,
        conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        sender_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        recipient_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        body            TEXT NOT NULL,
        is_read         BOOLEAN DEFAULT FALSE,
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT direct_messages_not_self CHECK (sender_id <> recipient_id),
        CONSTRAINT direct_messages_body_length CHECK (char_length(body) <= 2000)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_dm_conversation_created ON direct_messages (conversation_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_dm_recipient_unread ON direct_messages (recipient_id, is_read)",
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
                 location="", website="", genres="", created_at=None,
                 profile_image_url="", cover_image_url=""):
        self.id = id
        self.username = username
        self.email = email
        self.password_hash = password_hash
        self.bio = bio or ""
        self.location = location or ""
        self.website = website or ""
        self.genres = genres or ""
        self.created_at = created_at
        self.profile_image_url = profile_image_url or ""
        self.cover_image_url = cover_image_url or ""

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

    def update_profile_image(self, url):
        """Kept separate from update_profile() so a failed/rejected image
        upload can never touch the text-profile fields, and vice versa."""
        with db_cursor(commit=True) as cur:
            cur.execute("UPDATE users SET profile_image_url = %s WHERE id = %s", (url, self.id))
        self.profile_image_url = url

    def update_cover_image(self, url):
        with db_cursor(commit=True) as cur:
            cur.execute("UPDATE users SET cover_image_url = %s WHERE id = %s", (url, self.id))
        self.cover_image_url = url

    _COLUMNS = ("id, username, email, password_hash, bio, location, website, genres, "
                "created_at, profile_image_url, cover_image_url")

    @classmethod
    def _from_row(cls, row):
        if not row:
            return None
        return cls(id=row[0], username=row[1], email=row[2], password_hash=row[3],
                   bio=row[4], location=row[5], website=row[6], genres=row[7],
                   created_at=row[8], profile_image_url=row[9], cover_image_url=row[10])

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

"""Self-hosted, privacy-respecting analytics for Waveline.

No third-party analytics service, no client-side tracking script. One row is
recorded per real HTML page view via a Flask ``after_request`` hook. No raw
IP address is ever stored — the visitor identifier is a hash of the IP, the
UTC date, the user agent and the app's SECRET_KEY, so it rotates every day
and never becomes a durable cross-day identifier.

Query helpers for the owner-only /admin/stats page live here too, all raw
parameterised SQL via the shared ``db_cursor`` — no ORM, matching the rest
of the project.
"""

import hashlib
import logging
import os
from datetime import datetime, timezone

from flask import request, current_app
from flask_login import current_user

from db import db_cursor

logger = logging.getLogger(__name__)

# Paths that should never generate a pageview row: static assets, JSON APIs,
# the Deezer preview endpoint, and the admin area itself (so the owner's own
# visits don't skew their own stats).
_SKIP_PREFIXES = ("/static/", "/api/", "/preview", "/admin/")
_SKIP_EXACT = {"/favicon.ico"}


# ── GeoIP (optional, graceful) ───────────────────────────────────────

_geo_reader = None
_geo_initialised = False


def init_geoip():
    """Load the optional GeoLite2-Country database once at startup.

    Safe to call even when GEOIP_DB_PATH is unset or the file is missing —
    country lookups just return None afterwards and every other part of
    analytics still works normally.
    """
    global _geo_reader, _geo_initialised
    _geo_initialised = True
    path = os.getenv("GEOIP_DB_PATH")
    if not path:
        logger.info("GEOIP_DB_PATH not set — analytics will record country as NULL.")
        return
    try:
        import geoip2.database
        _geo_reader = geoip2.database.Reader(path)
        logger.info("GeoIP database loaded from %s", path)
    except Exception as e:
        logger.warning("GeoIP disabled: could not load database at %s (%s)", path, e)
        _geo_reader = None


def lookup_country(ip):
    """Return an ISO 3166-1 alpha-2 country code for ``ip``, or None."""
    if not _geo_initialised:
        init_geoip()
    if not _geo_reader or not ip:
        return None
    try:
        return _geo_reader.country(ip).country.iso_code
    except Exception:
        return None


# ── Recording ─────────────────────────────────────────────────────────

def _client_ip():
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or ""


def _visitor_hash(ip, user_agent, secret_key, when=None):
    """Daily-rotating visitor identifier. No raw IP is stored anywhere —
    only this truncated hash, which changes every UTC day."""
    day = (when or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    raw = f"{ip}|{day}|{user_agent}|{secret_key}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _should_record(response):
    if request.method != "GET":
        return False
    path = request.path
    if path in _SKIP_EXACT:
        return False
    if any(path.startswith(prefix) for prefix in _SKIP_PREFIXES):
        return False
    content_type = response.content_type or ""
    if not content_type.startswith("text/html"):
        return False
    return True


def record_pageview(response):
    """Flask ``after_request`` hook. Records at most one INSERT per request
    and must never raise into the request/response cycle — any failure
    (DB down, missing table before first boot, etc.) is swallowed."""
    try:
        if not _should_record(response):
            return response

        secret_key = current_app.secret_key or os.getenv("SECRET_KEY", "")
        ip = _client_ip()
        user_agent = request.headers.get("User-Agent", "")
        visitor_hash = _visitor_hash(ip, user_agent, secret_key)
        country = lookup_country(ip)
        user_id = current_user.id if current_user.is_authenticated else None

        with db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO analytics_events (path, referrer, country, user_id, visitor_hash) "
                "VALUES (%s, %s, %s, %s, %s)",
                (request.path, request.referrer, country, user_id, visitor_hash),
            )
    except Exception as e:
        logger.debug("Analytics recording skipped: %s", e)
    return response


# ── Stats query helpers (owner-only /admin/stats page) ─────────────────

def count_total_users():
    with db_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM users")
        return cur.fetchone()[0]


def signups_per_day(days=30):
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT DATE(created_at) AS day, COUNT(*) AS n
            FROM users
            WHERE created_at >= NOW() - (INTERVAL '1 day' * %s)
            GROUP BY day
            ORDER BY day
            """,
            (days,),
        )
        return [{"day": r[0], "count": r[1]} for r in cur.fetchall()]


def latest_signups(limit=25):
    with db_cursor() as cur:
        cur.execute(
            "SELECT username, created_at, location, website FROM users "
            "ORDER BY created_at DESC LIMIT %s",
            (limit,),
        )
        return [{"username": r[0], "created_at": r[1], "location": r[2], "website": r[3]}
                for r in cur.fetchall()]


def count_total_pageviews():
    with db_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM analytics_events")
        return cur.fetchone()[0]


def unique_visitors(days=None):
    """Distinct visitor_hash count. days=None means "today" (UTC calendar
    day); otherwise the trailing N days."""
    with db_cursor() as cur:
        if days is None:
            cur.execute(
                "SELECT COUNT(DISTINCT visitor_hash) FROM analytics_events "
                "WHERE DATE(created_at) = CURRENT_DATE"
            )
        else:
            cur.execute(
                "SELECT COUNT(DISTINCT visitor_hash) FROM analytics_events "
                "WHERE created_at >= NOW() - (INTERVAL '1 day' * %s)",
                (days,),
            )
        return cur.fetchone()[0]


def top_countries(days=30, limit=10):
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT country, COUNT(*) AS n
            FROM analytics_events
            WHERE created_at >= NOW() - (INTERVAL '1 day' * %s) AND country IS NOT NULL
            GROUP BY country
            ORDER BY n DESC
            LIMIT %s
            """,
            (days, limit),
        )
        return [{"country": r[0], "count": r[1]} for r in cur.fetchall()]


def top_paths(days=30, limit=10):
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT path, COUNT(*) AS n
            FROM analytics_events
            WHERE created_at >= NOW() - (INTERVAL '1 day' * %s)
            GROUP BY path
            ORDER BY n DESC
            LIMIT %s
            """,
            (days, limit),
        )
        return [{"path": r[0], "count": r[1]} for r in cur.fetchall()]


def top_referrers(days=30, limit=10):
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT referrer, COUNT(*) AS n
            FROM analytics_events
            WHERE created_at >= NOW() - (INTERVAL '1 day' * %s)
              AND referrer IS NOT NULL AND referrer != ''
            GROUP BY referrer
            ORDER BY n DESC
            LIMIT %s
            """,
            (days, limit),
        )
        return [{"referrer": r[0], "count": r[1]} for r in cur.fetchall()]


def count_total_searches():
    with db_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM searches")
        return cur.fetchone()[0]


def searches_by_membership():
    with db_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FILTER (WHERE user_id IS NOT NULL), "
            "COUNT(*) FILTER (WHERE user_id IS NULL) FROM searches"
        )
        members, anonymous = cur.fetchone()
        return {"members": members, "anonymous": anonymous}


def community_counts():
    with db_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM posts")
        posts = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM follows")
        follows = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM likes")
        likes = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM comments")
        comments = cur.fetchone()[0]
        return {"posts": posts, "follows": follows, "likes": likes, "comments": comments}


def get_stats():
    """Everything the /admin/stats page needs, in one call."""
    return {
        "total_users": count_total_users(),
        "signups_per_day": signups_per_day(30),
        "latest_signups": latest_signups(25),
        "total_pageviews": count_total_pageviews(),
        "unique_visitors_today": unique_visitors(None),
        "unique_visitors_7d": unique_visitors(7),
        "unique_visitors_30d": unique_visitors(30),
        "top_countries": top_countries(30, 10),
        "top_paths": top_paths(30, 10),
        "top_referrers": top_referrers(30, 10),
        "total_searches": count_total_searches(),
        "searches_by_membership": searches_by_membership(),
        "community": community_counts(),
    }

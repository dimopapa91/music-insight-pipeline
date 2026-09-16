"""Waveline — Flask application entry point.

This module now only wires the app together: configuration, login, Jinja
filters, and blueprint registration. Route handlers live in the ``views_*``
blueprints; shared logic lives in ``services.py``. Run with ``gunicorn
dashboard:app`` (see Procfile).
"""

import os

from flask import Flask, render_template, Response
from flask_login import LoginManager, current_user
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix

from models import User, init_db
from services import render_markdown, markdown_preview, artist_titlecase, timeago, avatar_color
from auth import auth_bp
from profiles import profiles_bp
from views_main import main_bp
from views_artist import artist_bp
from views_taste import taste_bp
from views_news import news_bp
from views_feed import feed_bp
from views_discover import discover_bp
from views_notifications import notifications_bp
from views_messages import messages_bp
from views_admin import admin_bp
from social import count_unread
from messaging import count_unread_messages
from analytics import record_pageview, init_geoip
from rate_limit import limiter

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-insecure-secret-change-me")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Only require HTTPS-only cookies in production (Railway sets RAILWAY_ENVIRONMENT)
    SESSION_COOKIE_SECURE=os.getenv("RAILWAY_ENVIRONMENT") is not None,
)

# Railway puts exactly one reverse proxy (its own edge/ingress) between the
# internet and this container, so x_for=1 trusts exactly one hop — matching
# the standard single-router PaaS architecture (same shape as Heroku's
# routing mesh). This is based on Railway's documented network model, not
# something empirically measured against a live production request from
# here; if you ever put a CDN or another proxy in front of Railway too, this
# needs to become x_for=2 (one hop per real proxy) or rate limiting will
# silently key on the wrong address again. Cheap one-time way to confirm the
# real hop count on live traffic: temporarily log request.headers.get(
# "X-Forwarded-For") for one real visit and count the comma-separated
# entries — do that before trusting this value in front of real abuse.
#
# WHY THIS MATTERS (do not "simplify" this away): Werkzeug's ProxyFix does
# NOT touch the X-Forwarded-For header itself — it only overwrites
# environ["REMOTE_ADDR"] with the (-x_for)th entry, i.e. the one added by
# the trusted proxy closest to us, discarding everything to its left as
# untrusted/possibly client-supplied. flask_limiter.util.get_remote_address
# reads request.remote_addr, so once this is in place it reads the correct,
# spoof-resistant value. analytics._client_ip() is a DIFFERENT, PRE-EXISTING
# helper that reads the raw X-Forwarded-For header directly and takes the
# FIRST (leftmost) entry — that value is attacker-supplied and trivially
# spoofable (an abuser can prepend any fake IP), which is fine for
# analytics' best-effort daily-rotating visitor hash but would be a real
# rate-limit bypass if reused here. Do not point the limiter's key_func at
# analytics._client_ip() or any left-most-entry parsing — keep
# get_remote_address() backed by ProxyFix-adjusted remote_addr.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# ── Rate limiting ───────────────────────────────────────────────────
# init_app() always runs with enabled=True so the storage backend actually
# gets created — Flask-Limiter's init_app() returns early and skips setting
# up storage entirely when RATELIMIT_ENABLED is false at call time, which
# would leave limiter.storage unusable even if something flips
# limiter.enabled back on afterwards. So: initialise for real, then decide
# whether to suppress enforcement via the instance attribute instead.
#
# conftest.py defaults RATELIMIT_ENABLED to "false" for the whole test
# session so 567+ unrelated tests don't go flaky sharing one IP-keyed
# in-memory counter; the enforcement tests flip limiter.enabled back to
# True directly (and reset limiter.storage first, since it's real).
limiter.init_app(app)
if os.getenv("RATELIMIT_ENABLED", "true").lower() == "false":
    limiter.enabled = False
# Static assets and robots.txt must always be reachable, crawlers included.
limiter.exempt(app.view_functions["static"])

# ── Login ───────────────────────────────────────────────────────────
login_manager = LoginManager(app)
login_manager.login_view = "auth.login"


@login_manager.user_loader
def load_user(user_id):
    try:
        return User.get(int(user_id))
    except Exception:
        return None


# ── Jinja display filters ───────────────────────────────────────────
app.jinja_env.filters["markdown"] = render_markdown
app.jinja_env.filters["markdown_preview"] = markdown_preview
app.jinja_env.filters["titlecase"] = artist_titlecase
app.jinja_env.filters["timeago"] = timeago
app.jinja_env.filters["avatar_color"] = avatar_color

# ── Blueprints ──────────────────────────────────────────────────────
app.register_blueprint(auth_bp)
app.register_blueprint(profiles_bp)
app.register_blueprint(main_bp)
app.register_blueprint(artist_bp)
app.register_blueprint(taste_bp)
app.register_blueprint(news_bp)
app.register_blueprint(feed_bp)
app.register_blueprint(discover_bp)
app.register_blueprint(notifications_bp)
app.register_blueprint(messages_bp)
app.register_blueprint(admin_bp)

# Self-hosted, privacy-respecting analytics: one row per real HTML page view.
# Never raises into the request/response cycle (see analytics.py).
app.after_request(record_pageview)


@app.context_processor
def inject_unread_notifications():
    """Make the unread-notifications/unread-messages badge counts available
    to every template. Each count is fetched independently so a hiccup
    fetching one (e.g. a transient DB issue) can't blank out the other."""
    if not current_user.is_authenticated:
        return {"unread_notifications": 0, "unread_messages": 0}
    try:
        notifications = count_unread(current_user.id)
    except Exception:
        notifications = 0
    try:
        messages = count_unread_messages(current_user.id)
    except Exception:
        messages = 0
    return {"unread_notifications": notifications, "unread_messages": messages}

@app.errorhandler(404)
def not_found(e):
    return render_template(
        "error.html",
        heading="Page not found",
        message="The page you're looking for doesn't exist, or may have moved.",
    ), 404


@app.errorhandler(500)
def server_error(e):
    return render_template(
        "error.html",
        heading="Something went wrong",
        message="This might be a temporary issue. Please try again in a moment.",
    ), 500


@app.errorhandler(429)
def rate_limited(e):
    return render_template(
        "error.html",
        heading="Slow down a moment",
        message="You've made a lot of requests in a short time. Please wait a bit and try again.",
    ), 429


# Disallows the routes that trigger real work (pipeline runs, Claude calls,
# private/account pages); allows the pages that are safe and worth indexing.
# A well-behaved crawler respecting this alone removes most of the abuse
# surface — see views_artist.py / views_main.py for the actual enforcement
# (auth gating + rate limits) for crawlers that don't.
_ROBOTS_TXT = """User-agent: *
Disallow: /compare
Disallow: /artist/
Disallow: /search
Disallow: /api/
Disallow: /admin/
Disallow: /messages
Disallow: /settings
Disallow: /notifications
Disallow: /u/
Allow: /
Allow: /about
Allow: /news
Allow: /discover
Allow: /feed
"""


@app.route("/robots.txt")
def robots_txt():
    return Response(_ROBOTS_TXT, mimetype="text/plain")


limiter.exempt(robots_txt)


# Ensure all application tables exist (idempotent — safe on every boot/worker).
init_db()

# Load the optional GeoLite2 database once at startup (see analytics.py —
# safe no-op if GEOIP_DB_PATH is unset or the file is missing).
init_geoip()


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("RAILWAY_ENVIRONMENT") is None  # debug off on Railway
    app.run(host="0.0.0.0", port=port, debug=debug)

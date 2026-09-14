"""Waveline — Flask application entry point.

This module now only wires the app together: configuration, login, Jinja
filters, and blueprint registration. Route handlers live in the ``views_*``
blueprints; shared logic lives in ``services.py``. Run with ``gunicorn
dashboard:app`` (see Procfile).
"""

import os

from flask import Flask, render_template
from flask_login import LoginManager, current_user
from dotenv import load_dotenv

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

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-insecure-secret-change-me")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Only require HTTPS-only cookies in production (Railway sets RAILWAY_ENVIRONMENT)
    SESSION_COOKIE_SECURE=os.getenv("RAILWAY_ENVIRONMENT") is not None,
)

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


# Ensure all application tables exist (idempotent — safe on every boot/worker).
init_db()

# Load the optional GeoLite2 database once at startup (see analytics.py —
# safe no-op if GEOIP_DB_PATH is unset or the file is missing).
init_geoip()


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("RAILWAY_ENVIRONMENT") is None  # debug off on Railway
    app.run(host="0.0.0.0", port=port, debug=debug)

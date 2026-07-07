"""Waveline — Flask application entry point.

This module now only wires the app together: configuration, login, Jinja
filters, and blueprint registration. Route handlers live in the ``views_*``
blueprints; shared logic lives in ``services.py``. Run with ``gunicorn
dashboard:app`` (see Procfile).
"""

import os

from flask import Flask
from flask_login import LoginManager, current_user
from dotenv import load_dotenv

from models import User, init_db
from services import render_markdown, markdown_preview, artist_titlecase, timeago
from auth import auth_bp
from profiles import profiles_bp
from views_main import main_bp
from views_artist import artist_bp
from views_taste import taste_bp
from views_news import news_bp
from views_feed import feed_bp
from views_notifications import notifications_bp
from social import count_unread

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

# ── Blueprints ──────────────────────────────────────────────────────
app.register_blueprint(auth_bp)
app.register_blueprint(profiles_bp)
app.register_blueprint(main_bp)
app.register_blueprint(artist_bp)
app.register_blueprint(taste_bp)
app.register_blueprint(news_bp)
app.register_blueprint(feed_bp)
app.register_blueprint(notifications_bp)


@app.context_processor
def inject_unread_notifications():
    """Make the unread-notifications badge count available to every template."""
    try:
        if current_user.is_authenticated:
            return {"unread_notifications": count_unread(current_user.id)}
    except Exception:
        pass
    return {"unread_notifications": 0}

# Ensure all application tables exist (idempotent — safe on every boot/worker).
init_db()


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("RAILWAY_ENVIRONMENT") is None  # debug off on Railway
    app.run(host="0.0.0.0", port=port, debug=debug)

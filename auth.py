"""Authentication blueprint: register, login, logout.

Passwords are hashed with Werkzeug; sessions are managed by Flask-Login.
CSRF is mitigated at the cookie level (SameSite=Lax, HttpOnly, Secure) which is
configured on the app in dashboard.py.
"""

import re

from flask import Blueprint, request, redirect, url_for, render_template
from flask_login import login_user, logout_user, login_required, current_user

from models import User
from rate_limit import limiter

auth_bp = Blueprint("auth", __name__)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,30}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate(username, email, password):
    errors = []
    if not USERNAME_RE.match(username or ""):
        errors.append("Username must be 3–30 letters, numbers or underscores.")
    if not EMAIL_RE.match(email or ""):
        errors.append("Enter a valid email address.")
    if len(password or "") < 8:
        errors.append("Password must be at least 8 characters.")
    return errors


# Bot sign-ups were appearing in Discover (random-string usernames, no
# activity). Two cheap layers: a per-IP limit on POSTs, and a honeypot
# field ("website") that people never see but form-spamming bots fill in.
# A bot that trips the honeypot gets a normal-looking redirect and no account.
@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("profiles.me"))
    errors, username, email = [], "", ""
    if request.method == "POST":
        if request.form.get("website", "").strip():
            return redirect(url_for("main.dashboard"))
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        errors = _validate(username, email, password)
        if not errors and User.get_by_username(username):
            errors.append("That username is already taken.")
        if not errors and User.get_by_email(email):
            errors.append("That email is already registered.")
        if not errors:
            user = User.create(username, email, password)
            login_user(user)
            return redirect(url_for("profiles.profile", username=user.username))
    return render_template("auth.html", mode="register", errors=errors, username=username, email=email)


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per 15 minutes", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("profiles.me"))
    errors, identifier = [], ""
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        password = request.form.get("password", "")
        user = User.get_by_username(identifier) or User.get_by_email(identifier)
        if user and user.check_password(password):
            login_user(user, remember=bool(request.form.get("remember")))
            next_url = request.args.get("next", "")
            # only allow local redirects
            if next_url.startswith("/") and not next_url.startswith("//"):
                return redirect(next_url)
            return redirect(url_for("profiles.profile", username=user.username))
        errors.append("Incorrect username/email or password.")
    return render_template("auth.html", mode="login", errors=errors, identifier=identifier)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("main.dashboard"))

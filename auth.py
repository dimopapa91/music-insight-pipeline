"""Authentication blueprint: register, login, logout.

Passwords are hashed with Werkzeug; sessions are managed by Flask-Login.
CSRF is mitigated at the cookie level (SameSite=Lax, HttpOnly, Secure) which is
configured on the app in dashboard.py.
"""

import re

from flask import Blueprint, request, redirect, url_for, render_template_string
from flask_login import login_user, logout_user, login_required, current_user

from models import User

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


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("profiles.me"))
    errors, username, email = [], "", ""
    if request.method == "POST":
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
    return render_template_string(
        AUTH_TEMPLATE, mode="register", errors=errors, username=username, email=email
    )


@auth_bp.route("/login", methods=["GET", "POST"])
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
    return render_template_string(
        AUTH_TEMPLATE, mode="login", errors=errors, identifier=identifier
    )


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("dashboard"))


AUTH_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{{ 'Sign up' if mode == 'register' else 'Log in' }} — Waveline</title>
    <link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
    <style>
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Space Mono', monospace; background: #e2e2df; color: #111; }
        .topbar { background: #111; color: #fff; padding: 0 24px; height: 44px; display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; }
        .topbar-brand { font-weight: 700; font-size: 0.95em; letter-spacing: 2px; color: #1da0c3; text-decoration: none; }
        .topbar-link { color: #1da0c3; font-size: 0.78em; text-decoration: none; }
        .topbar-link:hover { color: #fff; }
        .wrap { max-width: 400px; margin: 60px auto; padding: 0 20px; }
        .card { background: #fff; border: 1px solid #e8e8e8; border-radius: 6px; padding: 34px 32px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
        h1 { font-size: 1.5em; margin-bottom: 6px; }
        .sub { font-size: 0.78em; color: #777; margin-bottom: 24px; line-height: 1.6; }
        label { display: block; font-size: 0.72em; text-transform: uppercase; letter-spacing: 1px; color: #1da0c3; margin: 16px 0 6px; }
        input[type=text], input[type=email], input[type=password] {
            width: 100%; font-family: inherit; font-size: 0.92em; padding: 11px 13px;
            border: 1px solid #ddd; border-radius: 4px; background: #fafafa; color: #111;
        }
        input:focus { outline: none; border-color: #1da0c3; background: #fff; }
        .remember { display: flex; align-items: center; gap: 8px; font-size: 0.75em; color: #555; margin-top: 14px; }
        .remember input { width: auto; }
        button { width: 100%; margin-top: 22px; font-family: inherit; font-size: 0.82em; font-weight: 700;
            letter-spacing: 1px; text-transform: uppercase; color: #fff; background: #111; border: none;
            padding: 13px; border-radius: 4px; cursor: pointer; transition: background 0.15s; }
        button:hover { background: #1da0c3; }
        .errors { background: #fff3f3; border: 1px solid #f3c2c2; border-radius: 4px; padding: 12px 14px; margin-bottom: 18px; }
        .errors li { font-size: 0.75em; color: #b00; list-style: none; margin: 3px 0; }
        .swap { text-align: center; font-size: 0.76em; color: #777; margin-top: 22px; }
        .swap a { color: #1da0c3; text-decoration: none; }
    </style>
</head>
<body>
    <div class="topbar">
        <a href="/" class="topbar-brand">WAVELINE</a>
        <div><a href="/" class="topbar-link">← Dashboard</a></div>
    </div>
    <div class="wrap">
        <div class="card">
            {% if mode == 'register' %}
                <h1>Create your account</h1>
                <p class="sub">Build a music profile from what you search, and share discoveries with other listeners.</p>
            {% else %}
                <h1>Welcome back</h1>
                <p class="sub">Log in to your Waveline profile.</p>
            {% endif %}

            {% if errors %}
            <ul class="errors">
                {% for e in errors %}<li>{{ e }}</li>{% endfor %}
            </ul>
            {% endif %}

            {% if mode == 'register' %}
            <form method="POST" action="/register">
                <label>Username</label>
                <input type="text" name="username" value="{{ username }}" maxlength="30" required autofocus>
                <label>Email</label>
                <input type="email" name="email" value="{{ email }}" required>
                <label>Password</label>
                <input type="password" name="password" minlength="8" required>
                <button type="submit">Sign up</button>
            </form>
            <p class="swap">Already have an account? <a href="/login">Log in</a></p>
            {% else %}
            <form method="POST" action="/login">
                <label>Username or email</label>
                <input type="text" name="identifier" value="{{ identifier }}" required autofocus>
                <label>Password</label>
                <input type="password" name="password" required>
                <div class="remember"><input type="checkbox" name="remember" id="remember"><label for="remember" style="margin:0;text-transform:none;letter-spacing:0;color:#555;">Keep me logged in</label></div>
                <button type="submit">Log in</button>
            </form>
            <p class="swap">New here? <a href="/register">Create an account</a></p>
            {% endif %}
        </div>
    </div>
</body>
</html>
"""

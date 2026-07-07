"""Public profiles and profile settings.

A profile reuses the artists a user has searched (searches.user_id) so a new
account's page is meaningful as soon as they use the app. Posts / followers hook
in during Phase 2 (tables already exist).
"""

from flask import Blueprint, request, redirect, url_for, render_template_string, abort
from flask_login import login_required, current_user

from db import db_cursor
from models import User
from social import get_user_posts, get_follow_counts, toggle_follow, is_following

profiles_bp = Blueprint("profiles", __name__)


def get_user_searched_artists(user_id):
    with db_cursor() as cur:
        cur.execute(
            "SELECT DISTINCT artist_name FROM searches WHERE user_id = %s ORDER BY artist_name",
            (user_id,),
        )
        return [r[0] for r in cur.fetchall()]


@profiles_bp.route("/me")
@login_required
def me():
    return redirect(url_for("profiles.profile", username=current_user.username))


@profiles_bp.route("/u/<username>")
def profile(username):
    user = User.get_by_username(username)
    if not user:
        abort(404)
    viewer_id = current_user.id if current_user.is_authenticated else None
    artists = get_user_searched_artists(user.id)
    followers, following = get_follow_counts(user.id)
    posts = get_user_posts(user.id, viewer_id=viewer_id)
    is_own = current_user.is_authenticated and current_user.id == user.id
    following_this = bool(viewer_id and not is_own and is_following(viewer_id, user.id))
    return render_template_string(
        PROFILE_TEMPLATE,
        user=user, artists=artists, posts=posts,
        followers=followers, following=following,
        is_own=is_own, following_this=following_this,
    )


@profiles_bp.route("/u/<username>/follow", methods=["POST"])
@login_required
def follow(username):
    target = User.get_by_username(username)
    if target and target.id != current_user.id:
        toggle_follow(current_user.id, target.id)
    return redirect(url_for("profiles.profile", username=username))


@profiles_bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    saved = False
    if request.method == "POST":
        bio = request.form.get("bio", "").strip()[:500]
        current_user.update_bio(bio)
        saved = True
    return render_template_string(SETTINGS_TEMPLATE, user=current_user, saved=saved)


_TOPBAR = """
    <div class="topbar">
        <a href="/" class="topbar-brand">WAVELINE</a>
        <div class="topbar-right">
            <a href="/" class="topbar-link">Dashboard</a>
            <a href="/feed" class="topbar-link">Feed</a>
            {% if current_user.is_authenticated %}
                <a href="/me" class="topbar-link">@{{ current_user.username }}</a>
                <a href="/settings" class="topbar-link">Settings</a>
                <a href="/logout" class="topbar-link">Log out</a>
            {% else %}
                <a href="/login" class="topbar-link">Log in</a>
                <a href="/register" class="topbar-link">Sign up</a>
            {% endif %}
        </div>
    </div>
"""

_STYLE = """
    <style>
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Space Mono', monospace; background: #e2e2df; color: #111; }
        .topbar { background: #111; color: #fff; padding: 0 24px; height: 44px; display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 100; }
        .topbar-brand { font-weight: 700; font-size: 0.95em; letter-spacing: 2px; color: #1da0c3; text-decoration: none; }
        .topbar-right { display: flex; gap: 16px; align-items: center; }
        .topbar-link { color: #1da0c3; font-size: 0.78em; text-decoration: none; }
        .topbar-link:hover { color: #fff; }
        .wrap { max-width: 820px; margin: 0 auto; padding: 40px 24px; }
        .phead { display: flex; align-items: center; gap: 22px; background: #fff; border: 1px solid #e8e8e8; border-radius: 8px; padding: 28px 30px; }
        .avatar { width: 84px; height: 84px; border-radius: 50%; background: #1da0c3; color: #fff; display: flex; align-items: center; justify-content: center; font-size: 2.2em; font-weight: 700; flex-shrink: 0; }
        .pname { font-size: 1.5em; font-weight: 700; }
        .pmeta { font-size: 0.74em; color: #888; margin-top: 4px; }
        .pbio { font-size: 0.85em; color: #333; margin-top: 12px; line-height: 1.6; }
        .counts { display: flex; gap: 22px; margin-top: 14px; }
        .count b { font-size: 1.05em; }
        .count span { font-size: 0.72em; color: #888; }
        .editlink { font-size: 0.72em; color: #1da0c3; text-decoration: none; border: 1px solid #1da0c3; padding: 5px 12px; border-radius: 3px; }
        .editlink:hover { background: #1da0c3; color: #fff; }
        .section { background: #fff; border: 1px solid #e8e8e8; border-radius: 8px; padding: 24px 28px; margin-top: 22px; }
        .section-title { font-size: 0.72em; color: #1da0c3; letter-spacing: 1.5px; text-transform: uppercase; border-bottom: 1px solid #f0f0f0; padding-bottom: 10px; margin-bottom: 16px; }
        .chip-wrap { display: flex; flex-wrap: wrap; gap: 8px; }
        .chip { font-size: 0.78em; padding: 6px 13px; border: 1px solid #ddd; color: #333; border-radius: 3px; text-decoration: none; }
        .chip:hover { border-color: #1da0c3; color: #1da0c3; }
        .empty { font-size: 0.8em; color: #999; }
        label { display: block; font-size: 0.72em; text-transform: uppercase; letter-spacing: 1px; color: #1da0c3; margin-bottom: 8px; }
        textarea { width: 100%; min-height: 120px; font-family: inherit; font-size: 0.88em; padding: 12px 14px; border: 1px solid #ddd; border-radius: 4px; background: #fafafa; resize: vertical; }
        textarea:focus { outline: none; border-color: #1da0c3; background: #fff; }
        button { margin-top: 16px; font-family: inherit; font-size: 0.8em; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; color: #fff; background: #111; border: none; padding: 12px 26px; border-radius: 4px; cursor: pointer; }
        button:hover { background: #1da0c3; }
        .saved { font-size: 0.78em; color: #1a8f4c; margin-top: 12px; }
        .follow-btn { font-family: inherit; font-size: 0.72em; letter-spacing: 1px; text-transform: uppercase; font-weight: 700; color: #fff; background: #1da0c3; border: 1px solid #1da0c3; padding: 8px 18px; border-radius: 3px; cursor: pointer; margin: 0; }
        .follow-btn.following { background: #fff; color: #1da0c3; }
        .follow-btn:hover { opacity: 0.85; }
        .post { border: 1px solid #eee; border-radius: 6px; padding: 14px 16px; margin-bottom: 12px; }
        .post-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 6px; }
        .post-author { font-weight: 700; font-size: 0.85em; color: #111; text-decoration: none; }
        .post-author:hover { color: #1da0c3; }
        .post-artist { font-size: 0.7em; color: #1da0c3; text-decoration: none; border: 1px solid #1da0c3; border-radius: 3px; padding: 2px 8px; }
        .post-time { font-size: 0.66em; color: #999; margin-left: auto; }
        .post-body { font-size: 0.85em; line-height: 1.55; color: #222; white-space: pre-wrap; word-wrap: break-word; }
        .post-actions { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
        .inline { display: inline; }
        .act-btn { font-family: inherit; font-size: 0.72em; color: #666; background: none; border: 1px solid #e2e2e2; border-radius: 5px; padding: 3px 9px; cursor: pointer; margin: 0; }
        button.act-btn:hover { border-color: #1da0c3; color: #1da0c3; }
        .like-btn.liked { color: #e0245e; border-color: #e0245e; }
        button.del-btn { color: #b00; }
        button.del-btn:hover { border-color: #b00; color: #b00; }
        .comments { margin-top: 10px; border-top: 1px solid #f2f2f2; padding-top: 8px; }
        .comment { font-size: 0.8em; color: #333; padding: 4px 0; line-height: 1.5; }
        .c-author { font-weight: 700; color: #111; text-decoration: none; }
        .comment-form { display: flex; gap: 8px; margin-top: 8px; }
        .comment-form input { flex: 1; font-family: inherit; font-size: 0.8em; border: 1px solid #ddd; border-radius: 5px; padding: 7px 11px; background: #fafafa; }
        .comment-form input:focus { outline: none; border-color: #1da0c3; background: #fff; }
        .comment-form button { margin: 0; padding: 7px 16px; font-size: 0.72em; }
    </style>
"""

PROFILE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>@{{ user.username }} — Waveline</title>
    <link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
""" + _STYLE + """
</head>
<body>
""" + _TOPBAR + """
    <div class="wrap">
        <div class="phead">
            <div class="avatar">{{ user.username[0] | upper }}</div>
            <div style="flex:1;">
                <div class="pname">@{{ user.username }}</div>
                <div class="pmeta">
                    {% if user.created_at %}Joined {{ user.created_at.strftime('%B %Y') }}{% endif %}
                </div>
                {% if user.bio %}<div class="pbio">{{ user.bio }}</div>{% endif %}
                <div class="counts">
                    <div class="count"><b>{{ artists|length }}</b> <span>artists</span></div>
                    <div class="count"><b>{{ followers }}</b> <span>followers</span></div>
                    <div class="count"><b>{{ following }}</b> <span>following</span></div>
                </div>
            </div>
            {% if is_own %}<a href="/settings" class="editlink">Edit profile</a>
            {% elif current_user.is_authenticated %}
            <form method="POST" action="/u/{{ user.username }}/follow" class="inline">
                <button type="submit" class="follow-btn{{ ' following' if following_this }}">{{ 'Following' if following_this else 'Follow' }}</button>
            </form>
            {% endif %}
        </div>

        <div class="section">
            <div class="section-title">Artists {{ user.username }} has explored</div>
            {% if artists %}
            <div class="chip-wrap">
                {% for a in artists %}<a class="chip" href="/artist/{{ a }}">{{ a }}</a>{% endfor %}
            </div>
            {% else %}
            <p class="empty">No artists yet{% if is_own %} — search one on the <a href="/">dashboard</a> to start building your profile.{% endif %}</p>
            {% endif %}
        </div>

        <div class="section">
            <div class="section-title">Posts</div>
            {% if posts %}
                {% set next = '/u/' ~ user.username %}
                {% for post in posts %}{% include "_post_card.html" %}{% endfor %}
            {% else %}
            <p class="empty">No posts yet{% if is_own %} — share something on the <a href="/feed">feed</a>.{% endif %}</p>
            {% endif %}
        </div>
    </div>
</body>
</html>
"""

SETTINGS_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Settings — Waveline</title>
    <link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
""" + _STYLE + """
</head>
<body>
""" + _TOPBAR + """
    <div class="wrap">
        <div class="section">
            <div class="section-title">Edit profile</div>
            {% if saved %}<p class="saved">✓ Saved.</p>{% endif %}
            <form method="POST" action="/settings">
                <label>Bio</label>
                <textarea name="bio" maxlength="500" placeholder="Tell people what you're into…">{{ user.bio }}</textarea>
                <button type="submit">Save</button>
            </form>
            <p style="font-size:0.75em;color:#999;margin-top:18px;">Your public profile: <a href="/u/{{ user.username }}" style="color:#1da0c3;">/u/{{ user.username }}</a></p>
        </div>
    </div>
</body>
</html>
"""

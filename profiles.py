"""Public profiles and profile settings.

A profile reuses the artists a user has searched (searches.user_id) so a new
account's page is meaningful as soon as they use the app. Posts / followers hook
in during Phase 2 (tables already exist).
"""

from flask import Blueprint, request, redirect, url_for, render_template, abort
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
    return render_template(
        "profile.html",
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
        location = request.form.get("location", "").strip()[:120]
        website = request.form.get("website", "").strip()[:255]
        genres = request.form.get("genres", "").strip()[:255]
        current_user.update_profile(bio, location, website, genres)
        saved = True
    return render_template("settings.html", user=current_user, saved=saved)

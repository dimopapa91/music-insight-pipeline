"""Public profiles and profile settings.

A profile reuses the artists a user has searched (searches.user_id) so a new
account's page is meaningful as soon as they use the app. Posts / followers hook
in during Phase 2 (tables already exist).
"""

from flask import Blueprint, request, redirect, url_for, render_template, abort
from flask_login import login_required, current_user

import social_links
from db import db_cursor
from models import User
from social import get_user_posts, get_follow_counts, toggle_follow, is_following
from messaging import can_users_message
from image_storage import (
    is_image_storage_configured, upload_profile_image, upload_cover_image,
    ImageValidationError, ImageStorageError,
)

profiles_bp = Blueprint("profiles", __name__)


def get_user_searched_artists(user_id):
    """Artists this user searched, most recent first, one entry per artist
    ("SZA" and "Sza" used to show as two tiles)."""
    with db_cursor() as cur:
        cur.execute(
            "SELECT artist_name, MAX(searched_at) AS last FROM searches WHERE user_id = %s "
            "GROUP BY artist_name ORDER BY last DESC",
            (user_id,),
        )
        rows = cur.fetchall()
    seen, out = set(), []
    for name, _ in rows:
        key = " ".join((name or "").lower().split())
        if key and key not in seen:
            seen.add(key)
            out.append(name)
    return out


# The profile shows this many artists; the rest sit behind "Show all".
ARTISTS_SHOWN = 8


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
    can_message = bool(viewer_id and not is_own and can_users_message(viewer_id, user.id))
    links = social_links.links_for_display(user.id, user.website)
    return render_template(
        "profile.html",
        user=user, artists=artists, posts=posts, links=links, artists_shown=ARTISTS_SHOWN,
        followers=followers, following=following,
        is_own=is_own, following_this=following_this, can_message=can_message,
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
    link_errors = {}
    links = social_links.get_links(current_user.id)
    if request.method == "POST":
        bio = request.form.get("bio", "").strip()[:500]
        location = request.form.get("location", "").strip()[:120]
        website = request.form.get("website", "").strip()[:255]
        genres = request.form.get("genres", "").strip()[:255]
        # A mistyped link never costs the rest of the form: valid fields are
        # saved, an invalid link keeps its previous value and shows an error
        # next to the field with what the user typed.
        previous = dict(links)
        to_save, shown = {}, {}
        for platform in social_links.PLATFORMS:
            raw = request.form.get(f"link_{platform}", "")
            try:
                to_save[platform] = shown[platform] = social_links.normalize(platform, raw)
            except social_links.LinkError as e:
                link_errors[platform] = str(e)
                to_save[platform] = previous.get(platform, "")
                shown[platform] = raw.strip()
        current_user.update_profile(bio, location, website, genres)
        try:
            social_links.save_links(current_user.id, to_save)
        except Exception:
            link_errors["_all"] = "Your links couldn't be saved just now. Try again in a moment."
        links = {p: v for p, v in shown.items() if v}
        saved = True   # profile fields saved; any link problems are listed per field
    return render_template(
        "settings.html", user=current_user, saved=saved,
        links=links, link_errors=link_errors, platforms=social_links.PLATFORMS,
        image_storage_configured=is_image_storage_configured(),
    )


@profiles_bp.route("/settings/avatar", methods=["POST"])
@login_required
def upload_avatar():
    """Deliberately a separate route/form from /settings: a rejected or
    failed image upload must never touch the bio/location/website/genres
    fields, and a bad text-profile submission must never touch images."""
    file = request.files.get("image")
    try:
        url = upload_profile_image(current_user.id, file)
        current_user.update_profile_image(url)
        return redirect(url_for("profiles.settings", uploaded="avatar"))
    except (ImageValidationError, ImageStorageError) as e:
        # The existing profile_image_url is untouched — a failed upload
        # never erases a working image.
        return redirect(url_for("profiles.settings", upload_error=str(e)))


@profiles_bp.route("/settings/cover", methods=["POST"])
@login_required
def upload_cover():
    file = request.files.get("image")
    try:
        url = upload_cover_image(current_user.id, file)
        current_user.update_cover_image(url)
        return redirect(url_for("profiles.settings", uploaded="cover"))
    except (ImageValidationError, ImageStorageError) as e:
        return redirect(url_for("profiles.settings", upload_error=str(e)))

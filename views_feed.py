"""Community feed blueprint: posting, likes, comments, and the feed itself."""

from flask import Blueprint, render_template, request, redirect, url_for
from flask_login import login_required, current_user

from social import (
    create_post, delete_post, get_feed, toggle_like, add_comment,
)

feed_bp = Blueprint("feed", __name__)


def _safe_redirect(target, default):
    if target and target.startswith("/") and not target.startswith("//"):
        return redirect(target)
    return redirect(default)


PER_PAGE = 15


@feed_bp.route("/feed")
def feed():
    tab = request.args.get("tab")
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    viewer_id = current_user.id if current_user.is_authenticated else None
    if tab not in ("following", "discover"):
        tab = "following" if viewer_id else "discover"
    posts = get_feed(viewer_id, scope=tab, page=page, per_page=PER_PAGE)
    has_next = len(posts) == PER_PAGE
    return render_template("feed.html", posts=posts, tab=tab, page=page, has_next=has_next)


@feed_bp.route("/post", methods=["POST"])
@login_required
def create():
    body = request.form.get("body", "")
    artist = request.form.get("artist", "").strip() or None
    create_post(current_user.id, body, artist)
    return redirect(url_for("feed.feed"))


@feed_bp.route("/post/<int:post_id>/like", methods=["POST"])
@login_required
def like(post_id):
    toggle_like(current_user.id, post_id)
    return _safe_redirect(request.form.get("next"), url_for("feed.feed"))


@feed_bp.route("/post/<int:post_id>/comment", methods=["POST"])
@login_required
def comment(post_id):
    add_comment(current_user.id, post_id, request.form.get("body", ""))
    return _safe_redirect(request.form.get("next"), url_for("feed.feed"))


@feed_bp.route("/post/<int:post_id>/delete", methods=["POST"])
@login_required
def delete(post_id):
    delete_post(post_id, current_user.id)
    return _safe_redirect(request.form.get("next"), url_for("feed.feed"))

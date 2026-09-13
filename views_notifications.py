"""Notifications blueprint: list a user's notifications and mark them read."""

from flask import Blueprint, render_template
from flask_login import login_required, current_user

from social import get_notifications, mark_all_read

notifications_bp = Blueprint("notifications", __name__)


@notifications_bp.route("/notifications")
@login_required
def notifications():
    items = get_notifications(current_user.id)
    # Opening the page clears the unread badge.
    mark_all_read(current_user.id)
    return render_template("notifications.html", items=items)


@notifications_bp.route("/notifications/read", methods=["POST"])
@login_required
def notifications_read():
    """Desktop bell dropdown: fetch a short preview then mark it read.

    Always scoped to current_user.id (never a client-supplied id). Fetching
    before marking read (same order as the full /notifications page) lets the
    returned fragment still show which items were unread. Idempotent: a
    repeat call just re-marks already-read rows, a no-op.
    """
    items = get_notifications(current_user.id, limit=8)
    mark_all_read(current_user.id)
    return render_template("_notification_items.html", items=items)

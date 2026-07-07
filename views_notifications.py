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

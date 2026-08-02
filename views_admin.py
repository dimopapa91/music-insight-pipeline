"""Admin blueprint: owner-only self-hosted analytics dashboard.

Access requires both being logged in AND current_user.username matching the
ADMIN_USERNAME environment variable. Anyone else — including anonymous
visitors — gets a plain 404, not a redirect to login or a 403, so the
route's existence isn't revealed.
"""

import os

from flask import Blueprint, render_template, abort
from flask_login import current_user

import analytics

admin_bp = Blueprint("admin", __name__)


def _is_admin():
    admin_username = os.getenv("ADMIN_USERNAME")
    return bool(
        admin_username
        and current_user.is_authenticated
        and current_user.username == admin_username
    )


@admin_bp.route("/admin/stats")
def stats():
    if not _is_admin():
        abort(404)
    try:
        data = analytics.get_stats()
    except Exception:
        data = None
    return render_template("admin_stats.html", stats=data)

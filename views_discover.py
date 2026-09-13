"""Discover blueprint: find people worth following, ranked by musical taste
overlap and simple community signals. Everything comes from Waveline's own
Postgres data (see social.py) — no AI calls, no external music APIs.
"""

from flask import Blueprint, render_template, request, redirect, url_for
from flask_login import login_required, current_user

from models import User
from social import get_people_for_you, get_community_suggestions, search_people, toggle_follow

discover_bp = Blueprint("discover", __name__)

PEOPLE_FOR_YOU_LIMIT = 8
COMMUNITY_LIMIT = 12
SEARCH_LIMIT = 20
MAX_QUERY_LENGTH = 80


@discover_bp.route("/discover")
def discover():
    q = request.args.get("q", "").strip()[:MAX_QUERY_LENGTH]
    viewer_id = current_user.id if current_user.is_authenticated else None

    search_results = []
    people_for_you = []
    community = []
    personalised = False

    if q:
        search_results = search_people(q, viewer_id, limit=SEARCH_LIMIT)
    elif viewer_id:
        people_for_you = get_people_for_you(viewer_id, limit=PEOPLE_FOR_YOU_LIMIT)
        personalised = any(p["shared_count"] for p in people_for_you)
        exclude_ids = [viewer_id] + [p["id"] for p in people_for_you]
        community = get_community_suggestions(exclude_ids, limit=COMMUNITY_LIMIT, viewer_id=viewer_id)
    else:
        community = get_community_suggestions(limit=COMMUNITY_LIMIT)

    no_one_to_discover = not q and not people_for_you and not community

    return render_template(
        "discover.html",
        q=q,
        search_results=search_results,
        people_for_you=people_for_you,
        community=community,
        personalised=personalised,
        no_one_to_discover=no_one_to_discover,
    )


@discover_bp.route("/discover/u/<username>/follow", methods=["POST"])
@login_required
def discover_follow(username):
    target = User.get_by_username(username)
    if target and target.id != current_user.id:
        toggle_follow(current_user.id, target.id)
    q = request.form.get("q", "").strip()[:MAX_QUERY_LENGTH]
    if q:
        return redirect(url_for("discover.discover", q=q))
    return redirect(url_for("discover.discover"))

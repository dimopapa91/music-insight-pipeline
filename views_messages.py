"""Private messaging blueprint: inbox + one-to-one threads.

The mutual-follow anti-spam rule lives in messaging.py itself (see
messaging.can_users_message / send_direct_message), not here — this route
layer only handles HTTP concerns (auth, 404/403, redirects) and never
re-implements or weakens that rule.
"""

from flask import Blueprint, render_template, request, redirect, url_for, abort
from flask_login import login_required, current_user

from models import User
from messaging import (
    can_users_message, get_conversation_between, get_inbox, get_thread,
    send_direct_message, mark_conversation_read, MAX_BODY_LENGTH,
)

messages_bp = Blueprint("messages", __name__)

INBOX_LIMIT = 50
PREVIEW_LIMIT = 5
THREAD_LIMIT = 200


@messages_bp.route("/messages")
@login_required
def inbox():
    conversations = get_inbox(current_user.id, limit=INBOX_LIMIT)
    return render_template("messages_inbox.html", conversations=conversations)


@messages_bp.route("/messages/preview")
@login_required
def preview():
    """Desktop Messages-bell dropdown fragment. Read-only: unlike the
    Notifications dropdown, this must NEVER mark anything read — Phase 6
    intentionally marks messages read only when a real thread is opened."""
    conversations = get_inbox(current_user.id, limit=PREVIEW_LIMIT)
    return render_template("_message_items.html", conversations=conversations)


@messages_bp.route("/messages/u/<username>")
@login_required
def thread(username):
    target = User.get_by_username(username)
    if not target or target.id == current_user.id:
        abort(404)

    conversation_id = get_conversation_between(current_user.id, target.id, create=False)
    mutual = can_users_message(current_user.id, target.id)
    if conversation_id is None and not mutual:
        abort(403)

    conversation_id, thread_messages = get_thread(current_user.id, target.id, limit=THREAD_LIMIT)
    if conversation_id is not None:
        # Only this conversation's incoming unread messages — see
        # mark_conversation_read's own docstring for the exact scope.
        mark_conversation_read(conversation_id, current_user.id)

    return render_template(
        "messages_thread.html", target=target, messages=thread_messages,
        can_send=mutual, max_body_length=MAX_BODY_LENGTH,
    )


@messages_bp.route("/messages/u/<username>", methods=["POST"])
@login_required
def send(username):
    target = User.get_by_username(username)
    if not target or target.id == current_user.id:
        abort(404)
    # send_direct_message() is the sole authority here: it re-derives sender
    # from current_user.id, verifies mutual follow itself, and silently
    # no-ops on an invalid/oversized/self message rather than inserting.
    send_direct_message(current_user.id, target.id, request.form.get("body", ""))
    return redirect(url_for("messages.thread", username=username, _anchor="messages-end"))

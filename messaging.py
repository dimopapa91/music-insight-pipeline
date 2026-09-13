"""Private one-to-one messaging between mutually-following Waveline users.

Raw parameterised psycopg2 via the shared ``db_cursor`` helper, matching the
rest of the project — no ORM.

Anti-spam rule: users may only send messages while they *currently* mutually
follow each other (see ``can_users_message``). That rule is enforced here,
in the data/service layer, not just checked in the route layer, so no future
route change can accidentally bypass it. Existing conversation history stays
readable after an unfollow — only *new* sends are blocked (see
``send_direct_message``).

Never logs message bodies.
"""

from db import db_cursor

MAX_BODY_LENGTH = 2000


def _canonical_pair(user_a, user_b):
    """The two participant ids in the fixed (smaller, larger) order the
    conversations table's CHECK/UNIQUE constraints require."""
    return (user_a, user_b) if user_a < user_b else (user_b, user_a)


def _can_users_message_with_cursor(cur, user_a, user_b):
    """Cursor-level version of can_users_message() for callers (namely
    send_direct_message) that must run this check inside their OWN
    transaction rather than opening a second, separate one."""
    if user_a is None or user_b is None or user_a == user_b:
        return False
    cur.execute("""
        SELECT EXISTS(SELECT 1 FROM follows WHERE follower_id = %(a)s AND followee_id = %(b)s)
           AND EXISTS(SELECT 1 FROM follows WHERE follower_id = %(b)s AND followee_id = %(a)s)
    """, {"a": user_a, "b": user_b})
    return bool(cur.fetchone()[0])


def can_users_message(user_a, user_b):
    """True only if both users currently follow each other. A user can never
    message themselves."""
    with db_cursor() as cur:
        return _can_users_message_with_cursor(cur, user_a, user_b)


def _get_conversation_with_cursor(cur, user_a, user_b):
    u1, u2 = _canonical_pair(user_a, user_b)
    cur.execute(
        "SELECT id FROM conversations WHERE user1_id = %s AND user2_id = %s",
        (u1, u2),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _get_or_create_conversation_with_cursor(cur, user_a, user_b):
    """Race-safe find-or-create in a single statement, inside the caller's
    own transaction. INSERT ... ON CONFLICT DO UPDATE (rather than DO
    NOTHING) always returns a row via RETURNING — whether this call created
    the row or another concurrent sender already had — so there's no
    separate SELECT-then-INSERT window where two callers could each believe
    they need to create it. The DO UPDATE itself is a harmless no-op (it
    just reassigns user1_id to the value it already has)."""
    u1, u2 = _canonical_pair(user_a, user_b)
    cur.execute(
        "INSERT INTO conversations (user1_id, user2_id) VALUES (%s, %s) "
        "ON CONFLICT (user1_id, user2_id) DO UPDATE SET user1_id = EXCLUDED.user1_id "
        "RETURNING id",
        (u1, u2),
    )
    return cur.fetchone()[0]


def get_conversation_between(user_a, user_b, create=False):
    """Return the id of the single canonical conversation between two users,
    or None if it doesn't exist and ``create`` is False.

    Always resolves strictly from the (user_a, user_b) pair itself — never
    from a client-supplied conversation id — so callers can't be tricked
    into reading someone else's conversation.
    """
    with db_cursor(commit=create) as cur:
        if create:
            return _get_or_create_conversation_with_cursor(cur, user_a, user_b)
        return _get_conversation_with_cursor(cur, user_a, user_b)


def send_direct_message(sender_id, recipient_id, body):
    """Validate, verify mutual follow, find-or-create the one canonical
    conversation, and insert the message — all inside exactly ONE
    commit=True transaction, so a failure at any DB step (in particular the
    final INSERT) rolls back everything, including a conversation row that
    would otherwise have been newly created for this send.

    Deliberately does NOT call the public can_users_message() or
    get_conversation_between() — each opens its own separate db_cursor
    context, which would split this into multiple transactions and let a
    conversation be created even if the message insert then failed. The
    cursor-level `_..._with_cursor` helpers run inside this function's own
    transaction instead.

    Returns the new message id on success, or None if the send was rejected
    (self-message, empty/oversized body, or no mutual follow). Never logs
    ``body``.
    """
    if sender_id == recipient_id:
        return None
    body = (body or "").strip()
    if not body or len(body) > MAX_BODY_LENGTH:
        return None

    with db_cursor(commit=True) as cur:
        if not _can_users_message_with_cursor(cur, sender_id, recipient_id):
            return None
        conversation_id = _get_or_create_conversation_with_cursor(cur, sender_id, recipient_id)
        cur.execute(
            "INSERT INTO direct_messages (conversation_id, sender_id, recipient_id, body) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (conversation_id, sender_id, recipient_id, body),
        )
        return cur.fetchone()[0]


def get_inbox(user_id, limit=50):
    """One set-based query: every conversation the user is in, with the
    other participant's identity, the latest message, and the unread count
    for THIS user — sorted by most recent activity. No per-conversation
    follow-up queries (no N+1)."""
    with db_cursor() as cur:
        cur.execute("""
            SELECT c.id,
                   other.id, other.username, other.profile_image_url,
                   lm.body, lm.created_at, lm.sender_id,
                   COALESCE(unread.cnt, 0) AS unread_count
            FROM conversations c
            JOIN users other
              ON other.id = CASE WHEN c.user1_id = %(uid)s THEN c.user2_id ELSE c.user1_id END
            JOIN LATERAL (
                SELECT body, created_at, sender_id
                FROM direct_messages dm
                WHERE dm.conversation_id = c.id
                ORDER BY dm.created_at DESC
                LIMIT 1
            ) lm ON TRUE
            LEFT JOIN LATERAL (
                SELECT COUNT(*) AS cnt
                FROM direct_messages dm2
                WHERE dm2.conversation_id = c.id
                  AND dm2.recipient_id = %(uid)s AND dm2.is_read = FALSE
            ) unread ON TRUE
            WHERE c.user1_id = %(uid)s OR c.user2_id = %(uid)s
            ORDER BY lm.created_at DESC
            LIMIT %(limit)s
        """, {"uid": user_id, "limit": limit})
        return [{
            "conversation_id": r[0], "other_id": r[1], "username": r[2],
            "profile_image_url": r[3], "last_body": r[4], "last_created_at": r[5],
            "last_sender_id": r[6], "unread_count": r[7],
        } for r in cur.fetchall()]


def get_thread(user_id, other_user_id, limit=200):
    """The messages belonging to the single conversation between exactly
    these two users, oldest first, capped at the latest ``limit`` messages.

    Returns (conversation_id, messages) — conversation_id is None (and
    messages is []) if no conversation exists yet between them. Never
    resolves any conversation other than this exact canonical pair.
    """
    conversation_id = get_conversation_between(user_id, other_user_id, create=False)
    if conversation_id is None:
        return None, []
    with db_cursor() as cur:
        cur.execute("""
            SELECT id, sender_id, recipient_id, body, is_read, created_at
            FROM direct_messages
            WHERE conversation_id = %s
            ORDER BY created_at DESC
            LIMIT %s
        """, (conversation_id, limit))
        rows = cur.fetchall()
    rows.reverse()
    messages = [{"id": r[0], "sender_id": r[1], "recipient_id": r[2], "body": r[3],
                 "is_read": r[4], "created_at": r[5]} for r in rows]
    return conversation_id, messages


def mark_conversation_read(conversation_id, reader_id):
    """Marks only THIS conversation's incoming (recipient_id = reader_id)
    unread messages as read. Never touches other conversations, and never
    marks the reader's own sent messages (recipient_id excludes those)."""
    with db_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE direct_messages SET is_read = TRUE "
            "WHERE conversation_id = %s AND recipient_id = %s AND is_read = FALSE",
            (conversation_id, reader_id),
        )


def count_unread_messages(user_id):
    with db_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM direct_messages WHERE recipient_id = %s AND is_read = FALSE",
            (user_id,),
        )
        return cur.fetchone()[0]

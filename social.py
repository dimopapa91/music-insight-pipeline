"""Data layer for the community feed: posts, likes, comments, follows.

Raw psycopg2 via the shared ``db_cursor`` helper, matching the rest of the
project. All functions return plain dicts/tuples ready for templates.
"""

from db import db_cursor

# Common projection for a post row, including like/comment counts and whether
# the current viewer has liked it. Uses named params (%(viewer)s).
POST_SELECT = """
    SELECT p.id, p.body, p.artist, p.created_at, p.user_id, u.username,
           (SELECT COUNT(*) FROM likes l    WHERE l.post_id = p.id)  AS like_count,
           (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id)  AS comment_count,
           CASE WHEN %(viewer)s IS NULL THEN FALSE
                ELSE EXISTS (SELECT 1 FROM likes l2 WHERE l2.post_id = p.id AND l2.user_id = %(viewer)s)
           END AS liked
    FROM posts p
    JOIN users u ON u.id = p.user_id
"""


def _row_to_post(r):
    return {
        "id": r[0], "body": r[1], "artist": r[2], "created_at": r[3],
        "user_id": r[4], "username": r[5],
        "like_count": r[6], "comment_count": r[7], "liked": r[8],
        "comments": [],
    }


def _attach_comments(cur, posts):
    ids = [p["id"] for p in posts]
    if not ids:
        return
    cur.execute("""
        SELECT c.post_id, u.username, c.body, c.created_at
        FROM comments c JOIN users u ON u.id = c.user_id
        WHERE c.post_id = ANY(%s)
        ORDER BY c.created_at ASC
    """, (ids,))
    by_post = {}
    for post_id, username, body, created_at in cur.fetchall():
        by_post.setdefault(post_id, []).append(
            {"username": username, "body": body, "created_at": created_at})
    for p in posts:
        p["comments"] = by_post.get(p["id"], [])


# ── Posts ───────────────────────────────────────────────────────────

def create_post(user_id, body, artist=None):
    body = (body or "").strip()[:1000]
    if not body:
        return None
    with db_cursor(commit=True) as cur:
        cur.execute("INSERT INTO posts (user_id, body, artist) VALUES (%s, %s, %s) RETURNING id",
                    (user_id, body, (artist or None)))
        return cur.fetchone()[0]


def delete_post(post_id, user_id):
    """Delete a post only if it belongs to the user. Returns True if removed."""
    with db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM posts WHERE id = %s AND user_id = %s", (post_id, user_id))
        return cur.rowcount > 0


def get_feed(viewer_id, scope="discover", page=1, per_page=15):
    """scope='following' → viewer's own posts + people they follow; else global.
    Paginated: returns at most per_page posts for the given 1-based page."""
    page = max(1, int(page or 1))
    params = {"viewer": viewer_id, "limit": per_page, "offset": (page - 1) * per_page}
    if scope == "following" and viewer_id:
        where = (" WHERE p.user_id = %(viewer)s "
                 " OR p.user_id IN (SELECT followee_id FROM follows WHERE follower_id = %(viewer)s) ")
    else:
        where = ""
    sql = POST_SELECT + where + " ORDER BY p.created_at DESC LIMIT %(limit)s OFFSET %(offset)s"
    with db_cursor() as cur:
        cur.execute(sql, params)
        posts = [_row_to_post(r) for r in cur.fetchall()]
        _attach_comments(cur, posts)
    return posts


def get_user_posts(user_id, viewer_id=None, page=1, per_page=15):
    page = max(1, int(page or 1))
    params = {"viewer": viewer_id, "uid": user_id,
              "limit": per_page, "offset": (page - 1) * per_page}
    sql = POST_SELECT + " WHERE p.user_id = %(uid)s ORDER BY p.created_at DESC LIMIT %(limit)s OFFSET %(offset)s"
    with db_cursor() as cur:
        cur.execute(sql, params)
        posts = [_row_to_post(r) for r in cur.fetchall()]
        _attach_comments(cur, posts)
    return posts


# ── Likes ───────────────────────────────────────────────────────────

def toggle_like(user_id, post_id):
    with db_cursor(commit=True) as cur:
        cur.execute("SELECT 1 FROM likes WHERE user_id = %s AND post_id = %s", (user_id, post_id))
        if cur.fetchone():
            cur.execute("DELETE FROM likes WHERE user_id = %s AND post_id = %s", (user_id, post_id))
            liked = False
        else:
            cur.execute("INSERT INTO likes (user_id, post_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        (user_id, post_id))
            liked = True
            _notify_post_owner(cur, post_id, user_id, "like")
        cur.execute("SELECT COUNT(*) FROM likes WHERE post_id = %s", (post_id,))
        count = cur.fetchone()[0]
    return liked, count


# ── Comments ────────────────────────────────────────────────────────

def add_comment(user_id, post_id, body):
    body = (body or "").strip()[:500]
    if not body:
        return None
    with db_cursor(commit=True) as cur:
        cur.execute("INSERT INTO comments (post_id, user_id, body) VALUES (%s, %s, %s) RETURNING id",
                    (post_id, user_id, body))
        cid = cur.fetchone()[0]
        _notify_post_owner(cur, post_id, user_id, "comment")
        return cid


# ── Follows ─────────────────────────────────────────────────────────

def toggle_follow(follower_id, followee_id):
    """Follow/unfollow. Returns True if now following. No-op on self-follow."""
    if follower_id == followee_id:
        return False
    with db_cursor(commit=True) as cur:
        cur.execute("SELECT 1 FROM follows WHERE follower_id = %s AND followee_id = %s",
                    (follower_id, followee_id))
        if cur.fetchone():
            cur.execute("DELETE FROM follows WHERE follower_id = %s AND followee_id = %s",
                        (follower_id, followee_id))
            return False
        cur.execute("INSERT INTO follows (follower_id, followee_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (follower_id, followee_id))
        cur.execute(
            "INSERT INTO notifications (user_id, actor_id, type) VALUES (%s, %s, 'follow')",
            (followee_id, follower_id))
        return True


def is_following(follower_id, followee_id):
    with db_cursor() as cur:
        cur.execute("SELECT 1 FROM follows WHERE follower_id = %s AND followee_id = %s",
                    (follower_id, followee_id))
        return cur.fetchone() is not None


def get_follow_counts(user_id):
    with db_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM follows WHERE followee_id = %s", (user_id,))
        followers = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM follows WHERE follower_id = %s", (user_id,))
        following = cur.fetchone()[0]
    return followers, following


# ── Notifications ───────────────────────────────────────────────────

def _notify_post_owner(cur, post_id, actor_id, kind):
    """Insert a like/comment notification for a post's owner (skips self-actions).
    Runs inside an existing cursor/transaction."""
    cur.execute("SELECT user_id FROM posts WHERE id = %s", (post_id,))
    row = cur.fetchone()
    if row and row[0] != actor_id:
        cur.execute(
            "INSERT INTO notifications (user_id, actor_id, type, post_id) VALUES (%s, %s, %s, %s)",
            (row[0], actor_id, kind, post_id))


def get_notifications(user_id, limit=30):
    with db_cursor() as cur:
        cur.execute("""
            SELECT n.id, n.type, n.post_id, n.is_read, n.created_at, u.username
            FROM notifications n
            JOIN users u ON u.id = n.actor_id
            WHERE n.user_id = %s
            ORDER BY n.created_at DESC
            LIMIT %s
        """, (user_id, limit))
        return [{"id": r[0], "type": r[1], "post_id": r[2], "is_read": r[3],
                 "created_at": r[4], "actor": r[5]} for r in cur.fetchall()]


def count_unread(user_id):
    with db_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM notifications WHERE user_id = %s AND is_read = FALSE",
                    (user_id,))
        return cur.fetchone()[0]


def mark_all_read(user_id):
    with db_cursor(commit=True) as cur:
        cur.execute("UPDATE notifications SET is_read = TRUE WHERE user_id = %s AND is_read = FALSE",
                    (user_id,))

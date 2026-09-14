"""Data layer for the community feed: posts, likes, comments, follows.

Raw psycopg2 via the shared ``db_cursor`` helper, matching the rest of the
project. All functions return plain dicts/tuples ready for templates.
"""

from db import db_cursor

# Common projection for a post row, including the author's profile image,
# like/comment counts, and whether the current viewer has liked it. Uses
# named params (%(viewer)s).
POST_SELECT = """
    SELECT p.id, p.body, p.artist, p.created_at, p.user_id, u.username, u.profile_image_url,
           (SELECT COUNT(*) FROM likes l    WHERE l.post_id = p.id)  AS like_count,
           (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id)  AS comment_count,
           CASE WHEN %(viewer)s IS NULL THEN FALSE
                ELSE EXISTS (SELECT 1 FROM likes l2 WHERE l2.post_id = p.id AND l2.user_id = %(viewer)s)
           END AS liked
    FROM posts p
    JOIN users u ON u.id = p.user_id
"""

# How many of a post's most recent comments a compact card (feed/profile
# Activity) previews before offering a "View all N comments" link to the
# post's own detail page.
COMMENT_PREVIEW_LIMIT = 3


def _row_to_post(r):
    return {
        "id": r[0], "body": r[1], "artist": r[2], "created_at": r[3],
        "user_id": r[4], "username": r[5], "profile_image_url": r[6],
        "like_count": r[7], "comment_count": r[8], "liked": r[9],
        "comments": [], "has_more_comments": False,
    }


def _attach_comments(cur, posts, limit=None):
    """Batched comment loader — one query for every post passed in, never a
    query per post. When `limit` is given (feed/profile card previews),
    only each post's most recent `limit` comments are attached (still
    displayed oldest-first, via a ROW_NUMBER() window function scoped per
    post), and `has_more_comments` is set so the template can offer a
    "View all N comments" link. `limit=None` (post detail) loads every
    comment for the post.
    """
    ids = [p["id"] for p in posts]
    if not ids:
        return
    if limit is None:
        cur.execute("""
            SELECT c.post_id, u.username, c.body, c.created_at
            FROM comments c JOIN users u ON u.id = c.user_id
            WHERE c.post_id = ANY(%s)
            ORDER BY c.created_at ASC, c.id ASC
        """, (ids,))
        rows = cur.fetchall()
    else:
        cur.execute("""
            SELECT post_id, username, body, created_at FROM (
                SELECT c.post_id, u.username, c.body, c.created_at, c.id,
                       ROW_NUMBER() OVER (PARTITION BY c.post_id ORDER BY c.created_at DESC, c.id DESC) AS rn
                FROM comments c JOIN users u ON u.id = c.user_id
                WHERE c.post_id = ANY(%s)
            ) ranked
            WHERE rn <= %s
            ORDER BY post_id, created_at ASC, id ASC
        """, (ids, limit))
        rows = cur.fetchall()
    by_post = {}
    for post_id, username, body, created_at in rows:
        by_post.setdefault(post_id, []).append(
            {"username": username, "body": body, "created_at": created_at})
    for p in posts:
        shown = by_post.get(p["id"], [])
        p["comments"] = shown
        p["has_more_comments"] = limit is not None and p["comment_count"] > len(shown)


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


def get_feed(viewer_id, scope="latest", page=1, per_page=15, limit=None):
    """scope='following' → viewer's own posts + people they follow; anything
    else (the global chronological feed, "Latest") → all public posts.
    Deterministic ordering: created_at DESC, then id DESC as a tiebreaker.

    Paginated for the given 1-based page. `per_page` determines the OFFSET
    (i.e. what a "page" means); `limit` — defaulting to `per_page` — is how
    many rows are actually fetched. Callers doing exact-pagination (is
    there really a next page?) pass `limit=per_page + 1` and inspect
    whether more than `per_page` rows came back, without that probe row
    disturbing the OFFSET math for the *next* page.
    """
    page = max(1, int(page or 1))
    fetch_limit = per_page if limit is None else limit
    params = {"viewer": viewer_id, "limit": fetch_limit, "offset": (page - 1) * per_page}
    if scope == "following" and viewer_id:
        where = (" WHERE p.user_id = %(viewer)s "
                 " OR p.user_id IN (SELECT followee_id FROM follows WHERE follower_id = %(viewer)s) ")
    else:
        where = ""
    sql = POST_SELECT + where + " ORDER BY p.created_at DESC, p.id DESC LIMIT %(limit)s OFFSET %(offset)s"
    with db_cursor() as cur:
        cur.execute(sql, params)
        posts = [_row_to_post(r) for r in cur.fetchall()]
        _attach_comments(cur, posts, limit=COMMENT_PREVIEW_LIMIT)
    return posts


def get_user_posts(user_id, viewer_id=None, page=1, per_page=15):
    page = max(1, int(page or 1))
    params = {"viewer": viewer_id, "uid": user_id,
              "limit": per_page, "offset": (page - 1) * per_page}
    sql = POST_SELECT + " WHERE p.user_id = %(uid)s ORDER BY p.created_at DESC, p.id DESC LIMIT %(limit)s OFFSET %(offset)s"
    with db_cursor() as cur:
        cur.execute(sql, params)
        posts = [_row_to_post(r) for r in cur.fetchall()]
        _attach_comments(cur, posts, limit=COMMENT_PREVIEW_LIMIT)
    return posts


def get_post(post_id, viewer_id=None):
    """A single post plus ALL of its comments, for the public post-detail
    page. Returns None if the post doesn't exist. Never exposes anything
    beyond what POST_SELECT already exposes for the feed/profile."""
    with db_cursor() as cur:
        cur.execute(POST_SELECT + " WHERE p.id = %(post_id)s",
                    {"viewer": viewer_id, "post_id": post_id})
        row = cur.fetchone()
        if not row:
            return None
        post = _row_to_post(row)
        _attach_comments(cur, [post], limit=None)
    return post


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


def _notification_target_url(type_, actor, post_id):
    """Where clicking this notification should go. Follows go to the
    actor's profile; likes/comments go to the actual post (comments deep
    link straight to the discussion) now that post-detail pages exist —
    falling back to the feed only if a post_id is somehow missing."""
    if type_ == "follow":
        return f"/u/{actor}"
    if post_id:
        return f"/post/{post_id}" + ("#comments" if type_ == "comment" else "")
    return "/feed"


def get_notifications(user_id, limit=30):
    with db_cursor() as cur:
        cur.execute("""
            SELECT n.id, n.type, n.post_id, n.is_read, n.created_at, u.username, p.body,
                   u.profile_image_url
            FROM notifications n
            JOIN users u ON u.id = n.actor_id
            LEFT JOIN posts p ON p.id = n.post_id
            WHERE n.user_id = %s
            ORDER BY n.created_at DESC
            LIMIT %s
        """, (user_id, limit))
        return [{"id": r[0], "type": r[1], "post_id": r[2], "is_read": r[3],
                 "created_at": r[4], "actor": r[5], "post_body": r[6],
                 "actor_avatar": r[7],
                 "target_url": _notification_target_url(r[1], r[5], r[2])} for r in cur.fetchall()]


def count_unread(user_id):
    with db_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM notifications WHERE user_id = %s AND is_read = FALSE",
                    (user_id,))
        return cur.fetchone()[0]


def mark_all_read(user_id):
    with db_cursor(commit=True) as cur:
        cur.execute("UPDATE notifications SET is_read = TRUE WHERE user_id = %s AND is_read = FALSE",
                    (user_id,))


# ── Discover: taste-overlap recommendations + people search ──────────
#
# Everything here comes from Waveline's own Postgres data (searches.user_id,
# searches.artist_name, follows) — no AI/external API calls. Each function
# below is a small, fixed number of set-based queries; none of them loop
# over candidates and issue a query per row.

def _genre_list(genres):
    """Same splitting rule as models.User.genre_list, for the plain dicts
    the discover queries below return (they aren't User instances)."""
    return [g.strip() for g in (genres or "").split(",") if g.strip()]


def get_viewer_artist_set(user_id):
    """Distinct, case/whitespace-normalised artist names a user has searched."""
    with db_cursor() as cur:
        cur.execute(
            "SELECT DISTINCT LOWER(TRIM(artist_name)) FROM searches "
            "WHERE user_id = %s AND artist_name IS NOT NULL",
            (user_id,),
        )
        return [r[0] for r in cur.fetchall() if r[0]]


def _shared_artist_names(user_ids, viewer_artists):
    """One display-cased artist name per (user, normalised artist) pair,
    for the small set of candidates that actually have overlap."""
    if not user_ids or not viewer_artists:
        return {}
    with db_cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (s.user_id, LOWER(TRIM(s.artist_name))) s.user_id, s.artist_name
            FROM searches s
            WHERE s.user_id = ANY(%(ids)s) AND LOWER(TRIM(s.artist_name)) = ANY(%(artists)s)
            ORDER BY s.user_id, LOWER(TRIM(s.artist_name)), s.artist_name
        """, {"ids": list(user_ids), "artists": viewer_artists})
        by_user = {}
        for uid, name in cur.fetchall():
            by_user.setdefault(uid, []).append(name)
        return by_user


def get_community_suggestions(exclude_ids=None, limit=12, viewer_id=None, exclude_followed=False):
    """Simple, honest fallback/community ranking: most-followed, most-recently
    joined first. Used both as the "Explore the community" section and to pad
    out get_people_for_you() when taste-overlap alone doesn't fill the cap.

    `viewer_id` is only used to annotate each row with whether the viewer
    already follows them; pass `exclude_followed=True` to also filter those
    rows out entirely (used when padding a "not already followed" list).
    """
    with db_cursor() as cur:
        cur.execute("""
            SELECT u.id, u.username, u.bio, u.genres, u.profile_image_url,
                   (SELECT COUNT(*) FROM follows f WHERE f.followee_id = u.id) AS follower_count,
                   CASE WHEN %(viewer)s IS NULL THEN FALSE
                        ELSE EXISTS (SELECT 1 FROM follows f2
                                     WHERE f2.follower_id = %(viewer)s AND f2.followee_id = u.id)
                   END AS following
            FROM users u
            WHERE NOT (u.id = ANY(%(exclude)s))
              AND (
                    NOT %(exclude_followed)s
                    OR %(viewer)s IS NULL
                    OR NOT EXISTS (SELECT 1 FROM follows f3
                                   WHERE f3.follower_id = %(viewer)s AND f3.followee_id = u.id)
                  )
            ORDER BY follower_count DESC, u.created_at DESC, u.id DESC
            LIMIT %(limit)s
        """, {
            "exclude": list(exclude_ids or []),
            "viewer": viewer_id,
            "exclude_followed": bool(exclude_followed),
            "limit": limit,
        })
        return [{"id": r[0], "username": r[1], "bio": r[2], "genres": r[3],
                  "genre_list": _genre_list(r[3]),
                  "profile_image_url": r[4], "follower_count": r[5], "following": r[6],
                  "shared_count": 0, "shared_artists": []} for r in cur.fetchall()]


def get_people_for_you(viewer_id, limit=8):
    """Personalised "People for you": ranked by shared-artist overlap with the
    viewer's own search history, then follower count, then recency — all
    deterministic. Self and already-followed users are excluded. If overlap
    doesn't fill `limit` (little/no search history, or few matches), the rest
    is padded with honest community fallback candidates rather than leaving
    a broken/short section.
    """
    viewer_artists = get_viewer_artist_set(viewer_id)
    overlap = []
    if viewer_artists:
        with db_cursor() as cur:
            cur.execute("""
                SELECT u.id, u.username, u.bio, u.genres, u.profile_image_url,
                       COUNT(DISTINCT LOWER(TRIM(s.artist_name))) AS shared_count,
                       (SELECT COUNT(*) FROM follows f WHERE f.followee_id = u.id) AS follower_count
                FROM searches s
                JOIN users u ON u.id = s.user_id
                WHERE LOWER(TRIM(s.artist_name)) = ANY(%(artists)s)
                  AND u.id != %(viewer)s
                  AND NOT EXISTS (SELECT 1 FROM follows f2
                                  WHERE f2.follower_id = %(viewer)s AND f2.followee_id = u.id)
                GROUP BY u.id, u.username, u.bio, u.genres, u.profile_image_url
                ORDER BY shared_count DESC, follower_count DESC, u.id DESC
                LIMIT %(limit)s
            """, {"artists": viewer_artists, "viewer": viewer_id, "limit": limit})
            overlap = [{"id": r[0], "username": r[1], "bio": r[2], "genres": r[3],
                        "genre_list": _genre_list(r[3]),
                        "profile_image_url": r[4], "shared_count": r[5], "follower_count": r[6],
                        "following": False} for r in cur.fetchall()]

    result = list(overlap)
    if len(result) < limit:
        exclude_ids = [viewer_id] + [c["id"] for c in result]
        result.extend(get_community_suggestions(
            exclude_ids, limit=limit - len(result), viewer_id=viewer_id, exclude_followed=True,
        ))

    shared_ids = [c["id"] for c in result if c["shared_count"]]
    names_by_id = _shared_artist_names(shared_ids, viewer_artists)
    for c in result:
        c["shared_artists"] = names_by_id.get(c["id"], [])[:3]
    return result


def search_people(query, viewer_id=None, limit=20):
    """Case-insensitive people search over username/bio/genres (never email).
    `query` is capped defensively here too, even though callers should
    already cap it before it reaches this function."""
    q = (query or "").strip()[:80]
    if not q:
        return []
    escaped = q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    with db_cursor() as cur:
        cur.execute("""
            SELECT u.id, u.username, u.bio, u.genres, u.profile_image_url,
                   (SELECT COUNT(*) FROM follows f WHERE f.followee_id = u.id) AS follower_count,
                   CASE WHEN %(viewer)s IS NULL THEN FALSE
                        ELSE EXISTS (SELECT 1 FROM follows f2
                                     WHERE f2.follower_id = %(viewer)s AND f2.followee_id = u.id)
                   END AS following
            FROM users u
            WHERE (LOWER(u.username) LIKE %(pattern)s ESCAPE '\\'
                   OR LOWER(u.bio) LIKE %(pattern)s ESCAPE '\\'
                   OR LOWER(u.genres) LIKE %(pattern)s ESCAPE '\\')
              AND (%(viewer)s IS NULL OR u.id != %(viewer)s)
            ORDER BY u.username ASC
            LIMIT %(limit)s
        """, {"pattern": pattern, "viewer": viewer_id, "limit": limit})
        return [{"id": r[0], "username": r[1], "bio": r[2], "genres": r[3],
                  "genre_list": _genre_list(r[3]),
                  "profile_image_url": r[4], "follower_count": r[5], "following": r[6],
                  "shared_count": 0, "shared_artists": []} for r in cur.fetchall()]

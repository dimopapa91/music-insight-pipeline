"""Tests for Phase 7: feed refinement + focused post conversations.

Two layers, matching the rest of this suite's conventions:
- data layer (social.py): a fake db_cursor that records executed SQL/params
  (and, where relevant, returns canned fetchall/fetchone results), so query
  shape, ordering, and the comment-preview window-function logic can be
  asserted without ever touching a real database.
- route/template layer (views_feed.py, _post_card.html, post_detail.html,
  notification templates): the data functions are monkeypatched directly,
  matching test_feed.py/test_polish.py's existing conventions.

No network calls anywhere in this file.
"""

import contextlib
import datetime

import dashboard
import social
import views_feed
import views_notifications
import profiles
from models import User

OWNER = User(id=1, username="dimos", email="d@e.com", password_hash="x")
OTHER = User(id=2, username="alice", email="a@e.com", password_hash="x")


def _login(client, monkeypatch, user=OWNER):
    monkeypatch.setattr(User, "get", classmethod(lambda cls, i: user))
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)


def _sequential_db_cursor(monkeypatch, target, results):
    calls = []
    remaining = list(results)

    class FakeCur:
        def execute(self, sql, params=None):
            calls.append((sql, params))

        def fetchall(self):
            return remaining.pop(0) if remaining else []

        def fetchone(self):
            rows = remaining.pop(0) if remaining else []
            return rows[0] if rows else None

    @contextlib.contextmanager
    def cm(commit=False):
        yield FakeCur()

    monkeypatch.setattr(target, "db_cursor", cm)
    return calls


def _post(id=5, username="alice", body="Loving this new album", user_id=2,
          artist="Radiohead", profile_image_url="", like_count=3, comment_count=1,
          liked=False, comments=None, has_more_comments=False):
    return {
        "id": id, "body": body, "artist": artist,
        "created_at": datetime.datetime(2026, 7, 5, 12, 0),
        "user_id": user_id, "username": username, "profile_image_url": profile_image_url,
        "like_count": like_count, "comment_count": comment_count, "liked": liked,
        "comments": comments if comments is not None else
                    [{"username": "bob", "body": "totally agreed",
                      "created_at": datetime.datetime(2026, 7, 5, 12, 5)}],
        "has_more_comments": has_more_comments,
    }


# ── data layer: POST_SELECT / _row_to_post ──────────────────────────

def test_post_select_includes_author_profile_image_url():
    assert "u.profile_image_url" in social.POST_SELECT


def test_row_to_post_maps_profile_image_url():
    row = (5, "hi", "SZA", datetime.datetime(2026, 1, 1), 2, "alice",
           "https://res.cloudinary.com/demo/alice.jpg", 3, 1, False)
    post = social._row_to_post(row)
    assert post["profile_image_url"] == "https://res.cloudinary.com/demo/alice.jpg"
    assert post["username"] == "alice"
    assert post["has_more_comments"] is False


# ── data layer: feed ordering ────────────────────────────────────────

def test_latest_feed_ordering_is_deterministic(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, social, [[], []])
    social.get_feed(None, scope="latest", page=1, per_page=15)
    sql = calls[0][0]
    assert "ORDER BY p.created_at DESC, p.id DESC" in sql


def test_following_feed_ordering_is_the_same_deterministic_ordering(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, social, [[], []])
    social.get_feed(1, scope="following", page=1, per_page=15)
    sql = calls[0][0]
    assert "ORDER BY p.created_at DESC, p.id DESC" in sql


def test_following_scope_includes_viewers_own_posts(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, social, [[], []])
    social.get_feed(1, scope="following", page=1, per_page=15)
    sql = calls[0][0]
    assert "p.user_id = %(viewer)s" in sql


def test_following_scope_includes_followed_users(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, social, [[], []])
    social.get_feed(1, scope="following", page=1, per_page=15)
    sql = calls[0][0]
    assert "SELECT followee_id FROM follows WHERE follower_id = %(viewer)s" in sql


def test_latest_scope_has_no_following_filter_at_all(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, social, [[], []])
    social.get_feed(1, scope="latest", page=1, per_page=15)
    sql = calls[0][0]
    # the only per-user filtering clause references the follows table —
    # it must be entirely absent for the global chronological feed
    assert "follows" not in sql


def test_latest_scope_used_even_for_anonymous_viewer(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, social, [[], []])
    social.get_feed(None, scope="latest", page=1, per_page=15)
    assert "follows" not in calls[0][0]


# ── data layer: exact pagination ─────────────────────────────────────

def test_get_feed_limit_overrides_fetch_count_without_disturbing_offset(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, social, [[], []])
    social.get_feed(1, scope="latest", page=2, per_page=15, limit=16)
    sql, params = calls[0]
    assert params["limit"] == 16
    assert params["offset"] == 15  # (page-1)*per_page, NOT influenced by limit


def test_get_feed_default_limit_equals_per_page(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, social, [[], []])
    social.get_feed(1, scope="latest", page=1, per_page=15)
    assert calls[0][1]["limit"] == 15


# ── data layer: comment preview loader ───────────────────────────────

def test_comment_preview_is_a_single_batched_query(monkeypatch):
    calls, conn_calls = [], []

    class FakeCur:
        def execute(self, sql, params=None):
            calls.append((sql, params))

        def fetchall(self):
            return []

    posts = [{"id": 1, "comment_count": 0}, {"id": 2, "comment_count": 0}, {"id": 3, "comment_count": 0}]
    social._attach_comments(FakeCur(), posts, limit=3)
    assert len(calls) == 1  # one query for ALL posts, never one per post


def test_comment_preview_query_uses_row_number_window_function(monkeypatch):
    class FakeCur:
        def execute(self, sql, params=None):
            self.sql, self.params = sql, params

        def fetchall(self):
            return []

    cur = FakeCur()
    social._attach_comments(cur, [{"id": 1, "comment_count": 0}], limit=3)
    assert "ROW_NUMBER() OVER (PARTITION BY c.post_id" in cur.sql
    assert "WHERE rn <= %s" in cur.sql
    assert cur.params == ([1], 3)


def test_comment_preview_ranking_is_deterministic_on_tied_timestamps():
    # Two comments sharing the exact same created_at must still rank/select
    # deterministically rather than depending on undefined DB row order.
    class FakeCur:
        def execute(self, sql, params=None):
            self.sql = sql

        def fetchall(self):
            return []

    cur = FakeCur()
    social._attach_comments(cur, [{"id": 1, "comment_count": 0}], limit=3)
    assert "ORDER BY c.created_at DESC, c.id DESC" in cur.sql
    assert "ORDER BY post_id, created_at ASC, id ASC" in cur.sql


def test_full_comment_load_also_has_deterministic_ordering():
    class FakeCur:
        def execute(self, sql, params=None):
            self.sql = sql

        def fetchall(self):
            return []

    cur = FakeCur()
    social._attach_comments(cur, [{"id": 1, "comment_count": 0}], limit=None)
    assert "ORDER BY c.created_at ASC, c.id ASC" in cur.sql


def test_comment_preview_selects_most_recent_first_displays_chronological():
    class FakeCur:
        def execute(self, sql, params=None):
            self.sql = sql

        def fetchall(self):
            return [(1, "alice", "first", datetime.datetime(2026, 1, 1)),
                    (1, "bob", "second", datetime.datetime(2026, 1, 2)),
                    (1, "carol", "third", datetime.datetime(2026, 1, 3))]

    cur = FakeCur()
    posts = [{"id": 1, "comment_count": 5}]
    social._attach_comments(cur, posts, limit=3)
    # inner window ranks most-recent-first (DESC); outer SELECT re-sorts
    # the chosen rows chronologically (ASC) for natural reading order
    assert "ORDER BY c.created_at DESC" in cur.sql
    assert "ORDER BY post_id, created_at ASC" in cur.sql
    assert [c["body"] for c in posts[0]["comments"]] == ["first", "second", "third"]


def test_comment_preview_caps_at_three_per_post():
    assert social.COMMENT_PREVIEW_LIMIT == 3


def test_has_more_comments_true_when_comment_count_exceeds_preview():
    class FakeCur:
        def execute(self, sql, params=None):
            pass

        def fetchall(self):
            return [(1, "a", "x", datetime.datetime(2026, 1, 1)),
                    (1, "b", "y", datetime.datetime(2026, 1, 2)),
                    (1, "c", "z", datetime.datetime(2026, 1, 3))]

    posts = [{"id": 1, "comment_count": 8}]
    social._attach_comments(FakeCur(), posts, limit=3)
    assert posts[0]["has_more_comments"] is True


def test_has_more_comments_false_when_all_comments_shown():
    class FakeCur:
        def execute(self, sql, params=None):
            pass

        def fetchall(self):
            return [(1, "a", "x", datetime.datetime(2026, 1, 1))]

    posts = [{"id": 1, "comment_count": 1}]
    social._attach_comments(FakeCur(), posts, limit=3)
    assert posts[0]["has_more_comments"] is False


def test_zero_comment_post_works(monkeypatch):
    class FakeCur:
        def execute(self, sql, params=None):
            pass

        def fetchall(self):
            return []

    posts = [{"id": 1, "comment_count": 0}]
    social._attach_comments(FakeCur(), posts, limit=3)
    assert posts[0]["comments"] == []
    assert posts[0]["has_more_comments"] is False


def test_attach_comments_with_no_posts_issues_no_query():
    class FakeCur:
        def execute(self, sql, params=None):
            raise AssertionError("must not query when there are no posts")

    social._attach_comments(FakeCur(), [], limit=3)


# ── data layer: get_post / post detail ───────────────────────────────

def test_get_post_returns_the_matching_post(monkeypatch):
    row = (5, "hello", None, datetime.datetime(2026, 1, 1), 2, "alice", "", 0, 0, False)
    calls = _sequential_db_cursor(monkeypatch, social, [[row], []])
    post = social.get_post(5, viewer_id=1)
    assert post["id"] == 5
    assert post["username"] == "alice"
    assert calls[0][1] == {"viewer": 1, "post_id": 5}


def test_get_post_missing_returns_none(monkeypatch):
    _sequential_db_cursor(monkeypatch, social, [[]])
    assert social.get_post(9999) is None


def test_get_post_loads_all_comments_not_just_preview(monkeypatch):
    row = (5, "hello", None, datetime.datetime(2026, 1, 1), 2, "alice", "", 0, 5, False)
    calls = _sequential_db_cursor(monkeypatch, social, [[row], []])
    social.get_post(5)
    # the comments query for post detail must be the "load everything"
    # branch (no window function / rn <= limit clause)
    comments_sql = calls[1][0]
    assert "ROW_NUMBER" not in comments_sql
    assert "LIMIT" not in comments_sql.upper() or "rn <=" not in comments_sql


def test_get_post_liked_state_uses_viewer_param(monkeypatch):
    row = (5, "hello", None, datetime.datetime(2026, 1, 1), 2, "alice", "", 0, 0, True)
    calls = _sequential_db_cursor(monkeypatch, social, [[row], []])
    post = social.get_post(5, viewer_id=1)
    assert post["liked"] is True
    assert calls[0][1]["viewer"] == 1


def test_no_query_uses_string_formatted_ids_or_bodies():
    import inspect
    src = inspect.getsource(social)
    # crude but effective: no f-string/format/% "%s" % interpolation of a
    # variable directly into a SQL string outside the parameterised %s/%()s
    # placeholders already reviewed in prior phases
    assert 'f"SELECT' not in src
    assert 'f"INSERT' not in src
    assert 'f"UPDATE' not in src
    assert 'f"DELETE' not in src


# ── routes: /feed tab handling ───────────────────────────────────────

def _recording_get_feed(seen):
    def fake(viewer_id, scope="latest", page=1, per_page=15, limit=None):
        seen["scope"] = scope
        return []
    return fake


def test_feed_defaults_to_following_for_authenticated(monkeypatch):
    seen = {}
    monkeypatch.setattr(views_feed, "get_feed", _recording_get_feed(seen))
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.get("/feed")
    assert resp.status_code == 200
    assert seen["scope"] == "following"


def test_feed_defaults_to_latest_for_anonymous(monkeypatch):
    seen = {}
    monkeypatch.setattr(views_feed, "get_feed", _recording_get_feed(seen))
    client = dashboard.app.test_client()
    resp = client.get("/feed")
    assert resp.status_code == 200
    assert seen["scope"] == "latest"


def test_anonymous_tab_following_normalises_to_latest(monkeypatch):
    monkeypatch.setattr(views_feed, "get_feed", lambda *a, **k: [])
    client = dashboard.app.test_client()
    html = client.get("/feed?tab=following").data.decode()
    assert '>Following<' not in html
    assert 'aria-current="page">Latest<' in html


def test_tab_latest_works(monkeypatch):
    seen = {}
    monkeypatch.setattr(views_feed, "get_feed", _recording_get_feed(seen))
    client = dashboard.app.test_client()
    resp = client.get("/feed?tab=latest")
    assert resp.status_code == 200
    assert seen["scope"] == "latest"


def test_legacy_tab_discover_redirects_safely(monkeypatch):
    monkeypatch.setattr(views_feed, "get_feed", lambda *a, **k: [])
    client = dashboard.app.test_client()
    resp = client.get("/feed?tab=discover&page=3")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/feed?tab=latest&page=3"


def test_pager_urls_preserve_selected_tab(monkeypatch):
    monkeypatch.setattr(views_feed, "get_feed",
                        lambda viewer_id, scope="latest", page=1, per_page=15, limit=None:
                        [_post(id=i) for i in range(16)])
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/feed?tab=following&page=1").data.decode()
    assert 'href="/feed?tab=following&page=2"' in html


# ── routes: /post/<id> detail page ───────────────────────────────────

def test_post_detail_renders_publicly_for_anonymous(monkeypatch):
    monkeypatch.setattr(views_feed, "get_post", lambda post_id, viewer_id=None: _post(id=post_id))
    client = dashboard.app.test_client()
    resp = client.get("/post/5")
    assert resp.status_code == 200
    assert b"Loving this new album" in resp.data


def test_post_detail_missing_post_404s(monkeypatch):
    monkeypatch.setattr(views_feed, "get_post", lambda post_id, viewer_id=None: None)
    client = dashboard.app.test_client()
    resp = client.get("/post/9999")
    assert resp.status_code == 404


def test_post_detail_shows_all_comments(monkeypatch):
    many_comments = [{"username": f"u{i}", "body": f"comment {i}",
                       "created_at": datetime.datetime(2026, 1, 1)} for i in range(10)]
    monkeypatch.setattr(views_feed, "get_post",
                        lambda post_id, viewer_id=None: _post(id=post_id, comments=many_comments, comment_count=10))
    client = dashboard.app.test_client()
    html = client.get("/post/5").data.decode()
    for i in range(10):
        assert f"comment {i}" in html
    assert "View all" not in html  # already showing everything


def test_post_detail_shows_comment_form_when_authenticated(monkeypatch):
    monkeypatch.setattr(views_feed, "get_post", lambda post_id, viewer_id=None: _post(id=post_id))
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/post/5").data.decode()
    assert '<textarea' in html or '<input type="text" id="comment-5"' in html


def test_post_detail_hides_comment_form_when_anonymous(monkeypatch):
    monkeypatch.setattr(views_feed, "get_post", lambda post_id, viewer_id=None: _post(id=post_id))
    client = dashboard.app.test_client()
    html = client.get("/post/5").data.decode()
    assert 'id="comment-5"' not in html


def test_post_detail_has_comments_anchor_id():
    with open("templates/post_detail.html") as f:
        assert "comments_anchor_id" in f.read()


# ── routes: like/comment/delete redirects ────────────────────────────

def test_feed_like_redirect_stays_on_same_feed_tab_and_page(monkeypatch):
    monkeypatch.setattr(views_feed, "toggle_like", lambda uid, pid: (True, 1))
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.post("/post/5/like", data={"next": "/feed?tab=following&page=2"})
    assert resp.headers["Location"] == "/feed?tab=following&page=2"


def test_feed_comment_redirect_stays_on_same_feed_tab_and_page(monkeypatch):
    monkeypatch.setattr(views_feed, "add_comment", lambda uid, pid, body: 1)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.post("/post/5/comment", data={"body": "nice", "next": "/feed?tab=latest&page=3"})
    assert resp.headers["Location"] == "/feed?tab=latest&page=3"


def _extract_form(html, action):
    start = html.index(f'action="{action}"')
    end = html.index("</form>", start)
    return html[start:end]


def _extract_next_value(form_html):
    start = form_html.index('name="next" value="') + len('name="next" value="')
    end = form_html.index('"', start)
    return form_html[start:end]


def test_post_detail_like_form_targets_exactly_the_post_no_anchor(monkeypatch):
    # Like and Comment used to share one `next` value on post detail, which
    # made Like unnecessarily jump to #comments. They must now differ.
    monkeypatch.setattr(views_feed, "get_post", lambda post_id, viewer_id=None: _post(id=5, user_id=OWNER.id))
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    html = client.get("/post/5").data.decode()
    like_next = _extract_next_value(_extract_form(html, "/post/5/like"))
    assert like_next == "/post/5"
    assert like_next != "/post/5#comments"


def test_post_detail_comment_form_targets_comments_anchor(monkeypatch):
    monkeypatch.setattr(views_feed, "get_post", lambda post_id, viewer_id=None: _post(id=5, user_id=OWNER.id))
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    html = client.get("/post/5").data.decode()
    comment_next = _extract_next_value(_extract_form(html, "/post/5/comment"))
    assert comment_next == "/post/5#comments"


def test_post_detail_delete_form_targets_latest_feed_not_the_post(monkeypatch):
    monkeypatch.setattr(views_feed, "get_post", lambda post_id, viewer_id=None: _post(id=5, user_id=OWNER.id))
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    html = client.get("/post/5").data.decode()
    delete_next = _extract_next_value(_extract_form(html, "/post/5/delete"))
    assert delete_next == "/feed?tab=latest"
    assert delete_next != "/post/5"


def test_detail_like_redirect_returns_to_exactly_the_post(monkeypatch):
    monkeypatch.setattr(views_feed, "toggle_like", lambda uid, pid: (True, 1))
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.post("/post/5/like", data={"next": "/post/5"})
    assert resp.headers["Location"] == "/post/5"


def test_detail_comment_redirect_returns_to_comments_anchor(monkeypatch):
    monkeypatch.setattr(views_feed, "add_comment", lambda uid, pid, body: 1)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.post("/post/5/comment", data={"body": "nice", "next": "/post/5#comments"})
    assert resp.headers["Location"] == "/post/5#comments"


def test_successful_detail_delete_redirects_away_from_the_now_deleted_post(monkeypatch):
    # The whole point of a distinct delete_next: redirecting back to /post/5
    # after deleting post 5 would 404. Drive this through the real template
    # end to end — extract the actual rendered next value and post it.
    monkeypatch.setattr(views_feed, "get_post", lambda post_id, viewer_id=None: _post(id=5, user_id=OWNER.id))
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    html = client.get("/post/5").data.decode()
    delete_next = _extract_next_value(_extract_form(html, "/post/5/delete"))

    monkeypatch.setattr(views_feed, "delete_post", lambda pid, uid: True)
    resp = client.post("/post/5/delete", data={"next": delete_next})
    assert resp.headers["Location"] == "/feed?tab=latest"
    assert resp.headers["Location"] != "/post/5"


def test_feed_forms_still_use_the_shared_next_value(monkeypatch):
    # Feed/Profile callers set only `next` (no per-action overrides) and
    # must be completely unaffected by the post-detail action-specific vars.
    monkeypatch.setattr(views_feed, "get_feed",
                        lambda viewer_id, scope="latest", page=1, per_page=15, limit=None:
                        [_post(id=5, user_id=OWNER.id)])
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    html = client.get("/feed?tab=following&page=2").data.decode()
    expected = "/feed?tab=following&amp;page=2"  # Jinja HTML-escapes & in attribute values
    assert _extract_next_value(_extract_form(html, "/post/5/like")) == expected
    assert _extract_next_value(_extract_form(html, "/post/5/comment")) == expected
    assert _extract_next_value(_extract_form(html, "/post/5/delete")) == expected


def test_profile_activity_forms_still_use_the_shared_next_value(monkeypatch):
    monkeypatch.setattr(profiles, "get_user_searched_artists", lambda uid: [])
    monkeypatch.setattr(profiles, "get_follow_counts", lambda uid: (0, 0))
    monkeypatch.setattr(profiles, "get_user_posts", lambda uid, viewer_id=None, **k: [_post(id=5, user_id=OWNER.id)])
    monkeypatch.setattr(profiles, "is_following", lambda a, b: False)
    monkeypatch.setattr(profiles, "can_users_message", lambda a, b: False)
    monkeypatch.setattr(User, "get_by_username", classmethod(lambda cls, u: OWNER if u == "dimos" else None))
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    html = client.get("/u/dimos").data.decode()
    expected = "/u/dimos"
    assert _extract_next_value(_extract_form(html, "/post/5/like")) == expected
    assert _extract_next_value(_extract_form(html, "/post/5/comment")) == expected
    assert _extract_next_value(_extract_form(html, "/post/5/delete")) == expected


def test_no_arbitrary_external_redirect(monkeypatch):
    monkeypatch.setattr(views_feed, "toggle_like", lambda uid, pid: (True, 1))
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.post("/post/5/like", data={"next": "https://evil.example.com/phish"})
    assert resp.headers["Location"] == "/feed"  # falls back to the safe default


def test_no_protocol_relative_redirect(monkeypatch):
    monkeypatch.setattr(views_feed, "toggle_like", lambda uid, pid: (True, 1))
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.post("/post/5/like", data={"next": "//evil.example.com/phish"})
    assert resp.headers["Location"] == "/feed"


# ── delete ownership (data layer) ────────────────────────────────────

def test_delete_post_requires_ownership(monkeypatch):
    calls = []

    class FakeCur:
        rowcount = 1

        def execute(self, sql, params=None):
            calls.append((sql, params))

    @contextlib.contextmanager
    def cm(commit=False):
        yield FakeCur()

    monkeypatch.setattr(social, "db_cursor", cm)
    social.delete_post(5, 999)
    sql, params = calls[0]
    assert "user_id = %s" in sql
    assert params == (5, 999)


# ── UI: avatars on post cards (Phase 3/5 pattern) ────────────────────

def test_post_card_avatar_single_wrapper_with_genuinely_hidden_fallback(monkeypatch):
    monkeypatch.setattr(views_feed, "get_feed",
                        lambda viewer_id, scope="latest", page=1, per_page=15, limit=None:
                        [_post(id=42, profile_image_url="https://res.cloudinary.com/demo/alice.jpg")])
    client = dashboard.app.test_client()
    html = client.get("/feed?tab=latest").data.decode()
    assert html.count('class="wv-post-avatar"') == 1
    start = html.index('class="wv-post-avatar"')
    end = html.index('class="wv-post-author"', start)
    block = html[start:end]
    assert "<img" in block
    assert 'id="post-avatar-fallback-42"' in block
    assert "display:none" in block or "display: none" in block
    assert " hidden" not in block.split('id="post-avatar-fallback-42"')[1].split(">")[0]


def test_post_card_shows_monogram_without_image(monkeypatch):
    monkeypatch.setattr(views_feed, "get_feed",
                        lambda viewer_id, scope="latest", page=1, per_page=15, limit=None:
                        [_post(id=7, profile_image_url="")])
    client = dashboard.app.test_client()
    html = client.get("/feed?tab=latest").data.decode()
    start = html.index('class="wv-post-avatar"')
    end = html.index('class="wv-post-author"', start)
    block = html[start:end]
    assert "<img" not in block
    assert 'class="wv-avatar wv-avatar-xs"' in block


def test_post_author_heading_has_no_at_prefix(monkeypatch):
    monkeypatch.setattr(views_feed, "get_feed",
                        lambda viewer_id, scope="latest", page=1, per_page=15, limit=None: [_post()])
    client = dashboard.app.test_client()
    html = client.get("/feed?tab=latest").data.decode()
    assert '<a class="wv-post-author" href="/u/alice">alice</a>' in html


def test_timestamp_links_to_post_detail(monkeypatch):
    monkeypatch.setattr(views_feed, "get_feed",
                        lambda viewer_id, scope="latest", page=1, per_page=15, limit=None: [_post(id=5)])
    client = dashboard.app.test_client()
    html = client.get("/feed?tab=latest").data.decode()
    assert '<a class="wv-post-time" href="/post/5">' in html
    assert "<time" in html


def test_view_all_comments_appears_only_when_truncated(monkeypatch):
    monkeypatch.setattr(views_feed, "get_feed",
                        lambda viewer_id, scope="latest", page=1, per_page=15, limit=None:
                        [_post(id=5, comment_count=8, has_more_comments=True)])
    client = dashboard.app.test_client()
    html = client.get("/feed?tab=latest").data.decode()
    assert "View all 8 comments" in html
    assert 'href="/post/5#comments"' in html


def test_view_all_comments_absent_when_not_truncated(monkeypatch):
    monkeypatch.setattr(views_feed, "get_feed",
                        lambda viewer_id, scope="latest", page=1, per_page=15, limit=None:
                        [_post(id=5, comment_count=1, has_more_comments=False)])
    client = dashboard.app.test_client()
    html = client.get("/feed?tab=latest").data.decode()
    assert "View all" not in html


# ── UI: tabs / empty states ───────────────────────────────────────────

def test_following_and_latest_labels_for_authenticated(monkeypatch):
    monkeypatch.setattr(views_feed, "get_feed", lambda *a, **k: [])
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/feed").data.decode()
    assert ">Following<" in html
    assert ">Latest<" in html


def test_anonymous_feed_has_no_following_tab(monkeypatch):
    monkeypatch.setattr(views_feed, "get_feed", lambda *a, **k: [])
    client = dashboard.app.test_client()
    html = client.get("/feed").data.decode()
    tabs_start = html.index('class="cm-tabs"')
    tabs_end = html.index("</div>", tabs_start)
    tabs_block = html[tabs_start:tabs_end]
    assert ">Following<" not in tabs_block
    assert ">Latest<" in tabs_block


def test_global_tab_no_longer_labelled_discover(monkeypatch):
    monkeypatch.setattr(views_feed, "get_feed", lambda *a, **k: [])
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/feed").data.decode()
    tabs_start = html.index('class="cm-tabs"')
    tabs_end = html.index("</div>", tabs_start)
    assert ">Discover<" not in html[tabs_start:tabs_end]


def test_following_empty_state_links_to_discover(monkeypatch):
    monkeypatch.setattr(views_feed, "get_feed", lambda *a, **k: [])
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/feed?tab=following").data.decode()
    assert "Your Following feed is quiet" in html
    assert 'href="/discover"' in html


# ── notifications: deep links ─────────────────────────────────────────

def test_like_notification_links_to_post_detail():
    assert social._notification_target_url("like", "alice", 5) == "/post/5"


def test_comment_notification_links_to_post_detail_comments_anchor():
    assert social._notification_target_url("comment", "alice", 5) == "/post/5#comments"


def test_follow_notification_still_links_to_profile():
    assert social._notification_target_url("follow", "alice", None) == "/u/alice"


def test_notification_falls_back_to_feed_if_post_id_missing():
    assert social._notification_target_url("like", "alice", None) == "/feed"


def test_notifications_page_uses_target_url(monkeypatch):
    monkeypatch.setattr(views_notifications, "get_notifications", lambda uid, limit=30: [
        {"id": 1, "type": "like", "post_id": 5, "is_read": False,
         "created_at": datetime.datetime.utcnow(), "actor": "alice",
         "target_url": "/post/5"},
    ])
    monkeypatch.setattr(views_notifications, "mark_all_read", lambda uid: None)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/notifications").data.decode()
    assert 'href="/post/5"' in html


def test_notification_dropdown_fragment_uses_target_url(monkeypatch):
    monkeypatch.setattr(views_notifications, "get_notifications", lambda uid, limit=30: [
        {"id": 1, "type": "comment", "post_id": 5, "is_read": False,
         "created_at": datetime.datetime.utcnow(), "actor": "alice", "post_body": "hi",
         "actor_avatar": "", "target_url": "/post/5#comments"},
    ])
    monkeypatch.setattr(views_notifications, "mark_all_read", lambda uid: None)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.post("/notifications/read").data.decode()
    assert 'href="/post/5#comments"' in html


# ── regressions: profile Activity, escaping, other phases untouched ───

def test_profile_activity_still_renders_shared_post_card(monkeypatch):
    monkeypatch.setattr(profiles, "get_user_searched_artists", lambda uid: [])
    monkeypatch.setattr(profiles, "get_follow_counts", lambda uid: (0, 0))
    monkeypatch.setattr(profiles, "get_user_posts", lambda uid, viewer_id=None, **k: [_post()])
    monkeypatch.setattr(profiles, "is_following", lambda a, b: False)
    monkeypatch.setattr(profiles, "can_users_message", lambda a, b: False)
    monkeypatch.setattr(User, "get_by_username", classmethod(lambda cls, u: OWNER if u == "dimos" else None))
    client = dashboard.app.test_client()
    html = client.get("/u/dimos").data.decode()
    assert "Loving this new album" in html
    assert '<a class="wv-post-author" href="/u/alice">alice</a>' in html


def test_post_body_is_html_escaped(monkeypatch):
    hostile = _post(body="<script>alert(1)</script>")
    monkeypatch.setattr(views_feed, "get_feed",
                        lambda viewer_id, scope="latest", page=1, per_page=15, limit=None: [hostile])
    client = dashboard.app.test_client()
    html = client.get("/feed?tab=latest").data.decode()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_comment_body_is_html_escaped(monkeypatch):
    hostile_comment = [{"username": "bob", "body": "<b>bold</b>",
                         "created_at": datetime.datetime(2026, 1, 1)}]
    post = _post(comments=hostile_comment)
    monkeypatch.setattr(views_feed, "get_feed",
                        lambda viewer_id, scope="latest", page=1, per_page=15, limit=None: [post])
    client = dashboard.app.test_client()
    html = client.get("/feed?tab=latest").data.decode()
    assert "<b>bold</b>" not in html
    assert "&lt;b&gt;bold&lt;/b&gt;" in html


def test_post_card_never_uses_safe_or_markdown_filter():
    with open("templates/_post_card.html") as f:
        src = f.read()
    assert "post.body | safe" not in src
    assert "post.body | markdown" not in src
    assert "c.body | safe" not in src
    assert "c.body | markdown" not in src


def test_no_regression_to_notification_badge(monkeypatch):
    monkeypatch.setattr(dashboard, "count_unread", lambda uid: 4)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/about").data.decode()
    assert 'class="wv-badge">4' in html


def test_no_regression_to_unread_message_badge(monkeypatch):
    monkeypatch.setattr(dashboard, "count_unread_messages", lambda uid: 2)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/about").data.decode()
    assert 'aria-label="Messages (2 unread)"' in html


def test_discover_navigation_unchanged():
    client = dashboard.app.test_client()
    html = client.get("/about").data.decode()
    nav = html[html.index('<nav class="wv-nav"'):html.index("</nav>")]
    assert ">Discover<" in nav
    assert 'href="/discover"' in nav


def test_messages_nav_unchanged(monkeypatch):
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/about").data.decode()
    dock = html[html.index('<header class="wv-header"'):html.index("</header>")]
    assert 'href="/messages"' in dock


def test_notification_dropdown_semantics_unchanged():
    with open("templates/base.html") as f:
        src = f.read()
    assert 'aria-haspopup="dialog"' in src
    assert 'id="wv-notif-panel"' in src

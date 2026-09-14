"""Tests for the polish batch: relative time, richer profile, pagination, notifications."""

import contextlib
import datetime

import dashboard
import views_feed
import views_notifications
import profiles
import social
from models import User
from services import timeago

FAKE = User(id=1, username="dimos", email="d@e.com", password_hash="x")


def _login(client, monkeypatch, user=FAKE):
    monkeypatch.setattr(User, "get", classmethod(lambda cls, i: user))
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)


# ── relative timestamps ──

def test_timeago_buckets():
    now = datetime.datetime.utcnow()
    assert timeago(now - datetime.timedelta(seconds=5)) == "just now"
    assert timeago(now - datetime.timedelta(minutes=5)) == "5m ago"
    assert timeago(now - datetime.timedelta(hours=3)) == "3h ago"
    assert timeago(now - datetime.timedelta(days=2)) == "2d ago"
    assert timeago(None) == ""


# ── richer profile ──

def test_richer_profile_renders(monkeypatch):
    alice = User(id=2, username="alice", email="a@e.com", password_hash="x",
                 bio="jazz head", location="Berlin", website="https://alice.fm",
                 genres="jazz, ambient")
    monkeypatch.setattr(User, "get_by_username",
                        classmethod(lambda cls, u: alice if u == "alice" else None))
    monkeypatch.setattr(profiles, "get_user_searched_artists", lambda uid: [])
    monkeypatch.setattr(profiles, "get_follow_counts", lambda uid: (0, 0))
    monkeypatch.setattr(profiles, "get_user_posts", lambda uid, viewer_id=None, **k: [])
    monkeypatch.setattr(profiles, "is_following", lambda a, b: False)
    client = dashboard.app.test_client()
    resp = client.get("/u/alice")
    assert resp.status_code == 200
    assert b"Berlin" in resp.data
    assert b"alice.fm" in resp.data
    assert b"ambient" in resp.data  # genre chip


# ── pagination ──

def _posts(n):
    return [{
        "id": i, "body": f"post {i}", "artist": None,
        "created_at": datetime.datetime.utcnow(), "user_id": 9, "username": "bob",
        "like_count": 0, "comment_count": 0, "liked": False, "comments": [],
    } for i in range(n)]


def test_feed_exact_pagination_no_false_older_link_on_last_page(monkeypatch):
    # Regression: has_next used to be inferred from len(posts) == PER_PAGE,
    # which produced a false "Older" link whenever the final page happened
    # to contain exactly PER_PAGE rows. The route now requests PER_PAGE + 1
    # rows and only shows "Older" if more than PER_PAGE actually came back —
    # here there are genuinely only 15 posts total, so get_feed (which
    # respects `limit`, like the real one) returns all 15 even when asked
    # for up to 16.
    exactly_15 = _posts(15)
    monkeypatch.setattr(views_feed, "get_feed",
                        lambda viewer_id, scope="latest", page=1, per_page=15, limit=None: exactly_15[:limit])
    client = dashboard.app.test_client()
    resp = client.get("/feed?tab=latest&page=1")
    assert resp.status_code == 200
    assert b"Older" not in resp.data


def test_feed_shows_older_link_when_a_16th_post_exists(monkeypatch):
    sixteen = _posts(16)
    monkeypatch.setattr(views_feed, "get_feed",
                        lambda viewer_id, scope="latest", page=1, per_page=15, limit=None: sixteen[:limit])
    client = dashboard.app.test_client()
    resp = client.get("/feed?tab=latest&page=1")
    assert resp.status_code == 200
    assert b"Older" in resp.data
    # exactly 15 posts rendered, not 16
    assert resp.data.count(b'class="wv-post"') == 15


# ── notifications ──

def test_navbar_shows_unread_badge(monkeypatch):
    # Regression: the context processor must inject the unread count into the navbar.
    monkeypatch.setattr(dashboard, "count_unread", lambda uid: 4)
    monkeypatch.setattr(views_feed, "get_feed", lambda *a, **k: [])
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.get("/feed")
    assert b'class="wv-badge">4' in resp.data


def test_notifications_requires_login():
    client = dashboard.app.test_client()
    resp = client.get("/notifications")
    assert "/login" in resp.headers.get("Location", "")


def test_notifications_page_renders(monkeypatch):
    monkeypatch.setattr(views_notifications, "get_notifications",
                        lambda uid, limit=30: [{"id": 1, "type": "follow", "post_id": None,
                                                "is_read": False,
                                                "created_at": datetime.datetime.utcnow(),
                                                "actor": "alice"}])
    monkeypatch.setattr(views_notifications, "mark_all_read", lambda uid: None)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.get("/notifications")
    assert resp.status_code == 200
    assert b"@alice" in resp.data
    assert b"started following you" in resp.data


def test_follow_inserts_notification(monkeypatch):
    executed = []

    class FakeCur:
        def execute(self, sql, params=None):
            executed.append(sql)
        def fetchone(self):
            return None  # not currently following → triggers insert path

    @contextlib.contextmanager
    def fake_cm(commit=False):
        yield FakeCur()

    monkeypatch.setattr(social, "db_cursor", fake_cm)
    social.toggle_follow(1, 2)
    joined = " ".join(executed).lower()
    assert "insert into follows" in joined
    assert "insert into notifications" in joined

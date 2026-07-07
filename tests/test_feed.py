"""Feed, post, like, comment and follow route tests (social layer mocked)."""

import datetime

import dashboard
import views_feed
import profiles
from models import User

FAKE = User(id=1, username="dimos", email="d@e.com", password_hash="x")


def _login(client, monkeypatch, user=FAKE):
    monkeypatch.setattr(User, "get", classmethod(lambda cls, i: user))
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)


def _post(id=5, username="alice", body="Loving this new album", user_id=2):
    return {
        "id": id, "body": body, "artist": "Radiohead",
        "created_at": datetime.datetime(2026, 7, 5, 12, 0),
        "user_id": user_id, "username": username,
        "like_count": 3, "comment_count": 1, "liked": False,
        "comments": [{"username": "bob", "body": "totally agreed",
                      "created_at": datetime.datetime(2026, 7, 5, 12, 5)}],
    }


def test_feed_discover_renders_posts_and_comments(monkeypatch):
    monkeypatch.setattr(views_feed, "get_feed",
                        lambda viewer_id, scope="discover", limit=50: [_post()])
    client = dashboard.app.test_client()
    resp = client.get("/feed?tab=discover")
    assert resp.status_code == 200
    assert b"Loving this new album" in resp.data
    assert b"totally agreed" in resp.data
    assert b"@alice" in resp.data


def test_feed_logged_out_shows_signin_prompt(monkeypatch):
    monkeypatch.setattr(views_feed, "get_feed", lambda *a, **k: [])
    client = dashboard.app.test_client()
    resp = client.get("/feed")
    assert resp.status_code == 200
    assert b"/register" in resp.data


def test_create_post_requires_login():
    client = dashboard.app.test_client()
    resp = client.post("/post", data={"body": "hi"})
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("Location", "")


def test_create_post_when_logged_in(monkeypatch):
    seen = {}
    monkeypatch.setattr(views_feed, "create_post",
                        lambda uid, body, artist=None: seen.update(uid=uid, body=body, artist=artist) or 1)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.post("/post", data={"body": "New discovery", "artist": "SZA"})
    assert resp.status_code == 302 and "/feed" in resp.headers["Location"]
    assert seen == {"uid": 1, "body": "New discovery", "artist": "SZA"}


def test_like_requires_login():
    client = dashboard.app.test_client()
    resp = client.post("/post/5/like")
    assert "/login" in resp.headers.get("Location", "")


def test_like_toggles_when_logged_in(monkeypatch):
    seen = {}
    monkeypatch.setattr(views_feed, "toggle_like",
                        lambda uid, pid: seen.update(uid=uid, pid=pid) or (True, 1))
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.post("/post/5/like", data={"next": "/feed?tab=discover"})
    assert resp.status_code == 302
    assert seen == {"uid": 1, "pid": 5}
    assert "/feed" in resp.headers["Location"]


def test_follow_toggles(monkeypatch):
    target = User(id=2, username="alice", email="a@e.com", password_hash="x")
    monkeypatch.setattr(User, "get_by_username",
                        classmethod(lambda cls, u: target if u == "alice" else None))
    seen = {}
    monkeypatch.setattr(profiles, "toggle_follow",
                        lambda a, b: seen.update(a=a, b=b) or True)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.post("/u/alice/follow")
    assert resp.status_code == 302 and "/u/alice" in resp.headers["Location"]
    assert seen == {"a": 1, "b": 2}

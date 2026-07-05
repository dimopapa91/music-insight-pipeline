"""Auth + profile tests. The DB layer is mocked, so these exercise routing,
validation, password hashing, session login and template rendering without a
live database.
"""

from werkzeug.security import generate_password_hash

import dashboard
import profiles
from models import User
from auth import _validate


def _user(id=1, username="dimos", email="d@e.com", password="password123", bio=""):
    return User(id=id, username=username, email=email,
                password_hash=generate_password_hash(password), bio=bio)


# ---- pure logic ----

def test_validate_accepts_good_input():
    assert _validate("dimos", "d@e.com", "password123") == []


def test_validate_flags_bad_input():
    assert _validate("ab", "d@e.com", "password123")        # username too short
    assert _validate("dimos", "not-an-email", "password123")  # bad email
    assert _validate("dimos", "d@e.com", "short")            # weak password


def test_password_hash_roundtrip():
    u = _user(password="password123")
    assert u.check_password("password123")
    assert not u.check_password("wrongpassword")


# ---- register ----

def test_register_creates_account_and_redirects(monkeypatch):
    fake = _user()
    monkeypatch.setattr(User, "get_by_username", classmethod(lambda cls, u: None))
    monkeypatch.setattr(User, "get_by_email", classmethod(lambda cls, e: None))
    monkeypatch.setattr(User, "create", classmethod(lambda cls, u, e, p: fake))
    monkeypatch.setattr(User, "get", classmethod(lambda cls, i: fake))
    client = dashboard.app.test_client()
    resp = client.post("/register", data={
        "username": "dimos", "email": "d@e.com", "password": "password123"})
    assert resp.status_code == 302
    assert "/u/dimos" in resp.headers["Location"]


def test_register_rejects_taken_username(monkeypatch):
    monkeypatch.setattr(User, "get_by_username", classmethod(lambda cls, u: _user()))
    monkeypatch.setattr(User, "get_by_email", classmethod(lambda cls, e: None))
    client = dashboard.app.test_client()
    resp = client.post("/register", data={
        "username": "dimos", "email": "new@e.com", "password": "password123"})
    assert resp.status_code == 200
    assert b"already taken" in resp.data


# ---- login ----

def test_login_success_redirects_to_profile(monkeypatch):
    fake = _user()
    monkeypatch.setattr(User, "get_by_username", classmethod(lambda cls, u: fake if u == "dimos" else None))
    monkeypatch.setattr(User, "get_by_email", classmethod(lambda cls, e: None))
    monkeypatch.setattr(User, "get", classmethod(lambda cls, i: fake))
    client = dashboard.app.test_client()
    resp = client.post("/login", data={"identifier": "dimos", "password": "password123"})
    assert resp.status_code == 302
    assert "/u/dimos" in resp.headers["Location"]


def test_login_rejects_wrong_password(monkeypatch):
    monkeypatch.setattr(User, "get_by_username", classmethod(lambda cls, u: _user()))
    monkeypatch.setattr(User, "get_by_email", classmethod(lambda cls, e: None))
    client = dashboard.app.test_client()
    resp = client.post("/login", data={"identifier": "dimos", "password": "wrongpassword"})
    assert resp.status_code == 200
    assert b"Incorrect" in resp.data


# ---- profiles ----

def test_public_profile_renders(monkeypatch):
    fake = _user(id=2, username="alice", bio="I love jazz")
    monkeypatch.setattr(User, "get_by_username", classmethod(lambda cls, u: fake if u == "alice" else None))
    monkeypatch.setattr(profiles, "get_user_searched_artists", lambda uid: ["Radiohead", "SZA"])
    monkeypatch.setattr(profiles, "get_follow_counts", lambda uid: (0, 0))
    client = dashboard.app.test_client()
    resp = client.get("/u/alice")
    assert resp.status_code == 200
    assert b"@alice" in resp.data
    assert b"Radiohead" in resp.data
    assert b"I love jazz" in resp.data


def test_unknown_profile_404s(monkeypatch):
    monkeypatch.setattr(User, "get_by_username", classmethod(lambda cls, u: None))
    client = dashboard.app.test_client()
    resp = client.get("/u/ghost")
    assert resp.status_code == 404

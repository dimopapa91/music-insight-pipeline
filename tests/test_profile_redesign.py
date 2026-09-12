"""Tests for the Phase 2 public-profile redesign: cover hero, circular
avatar, identity block, and the new sub-navigation strip.

Verifies behaviour, not pixel-perfect styling: real destinations only, no
dead Followers/Following/Likes/Messages links, existing follow/edit-profile
logic unchanged, Artists explored links still resolve to /artist/<name>.
"""

import datetime

import dashboard
import profiles
from models import User

OWNER = User(id=1, username="dimos", email="d@e.com", password_hash="x",
             bio="Manchester, mostly ambient.", location="Manchester, UK",
             website="https://dimospapageorgiou.com", genres="ambient, jazz",
             created_at=datetime.datetime(2026, 1, 1))
VIEWER = User(id=2, username="alice", email="a@e.com", password_hash="x")


def _login(client, monkeypatch, user):
    monkeypatch.setattr(User, "get", classmethod(lambda cls, i: user))
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)


def _mock_profile_data(monkeypatch, artists=None, posts=None, followers=3, following=5,
                        is_following_result=False):
    monkeypatch.setattr(profiles, "get_user_searched_artists", lambda uid: artists or [])
    monkeypatch.setattr(profiles, "get_follow_counts", lambda uid: (followers, following))
    monkeypatch.setattr(profiles, "get_user_posts", lambda uid, viewer_id=None, **k: posts or [])
    monkeypatch.setattr(profiles, "is_following", lambda a, b: is_following_result)
    monkeypatch.setattr(User, "get_by_username",
                         classmethod(lambda cls, u: OWNER if u == "dimos" else None))


# ── Public profile still renders ──

def test_public_profile_renders(monkeypatch):
    _mock_profile_data(monkeypatch, artists=["Radiohead", "SZA"])
    client = dashboard.app.test_client()
    resp = client.get("/u/dimos")
    assert resp.status_code == 200
    assert b"@dimos" in resp.data
    assert b"Manchester, UK" in resp.data
    assert b"dimospapageorgiou.com" in resp.data


def test_unknown_profile_still_404s(monkeypatch):
    monkeypatch.setattr(User, "get_by_username", classmethod(lambda cls, u: None))
    client = dashboard.app.test_client()
    resp = client.get("/u/ghost")
    assert resp.status_code == 404


# ── Own profile vs. another user's profile actions ──

def test_own_profile_shows_edit_profile_not_follow(monkeypatch):
    _mock_profile_data(monkeypatch)
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    resp = client.get("/u/dimos")
    html = resp.data.decode()
    assert 'href="/settings"' in html
    assert "Edit profile" in html
    # Scoped to the action block specifically: the identity stats legitimately
    # include a "Following" stat *label* elsewhere on the page, which isn't
    # what this assertion is about.
    action_start = html.index('class="pf-action"')
    action_end = html.index("</div>", action_start)
    action_block = html[action_start:action_end]
    assert ">Follow<" not in action_block
    assert ">Following<" not in action_block


def test_other_profile_shows_follow_when_not_following(monkeypatch):
    _mock_profile_data(monkeypatch, is_following_result=False)
    client = dashboard.app.test_client()
    _login(client, monkeypatch, VIEWER)
    resp = client.get("/u/dimos")
    html = resp.data.decode()
    assert 'action="/u/dimos/follow"' in html
    assert ">Follow<" in html
    assert "Edit profile" not in html


def test_other_profile_shows_following_when_already_following(monkeypatch):
    _mock_profile_data(monkeypatch, is_following_result=True)
    client = dashboard.app.test_client()
    _login(client, monkeypatch, VIEWER)
    resp = client.get("/u/dimos")
    html = resp.data.decode()
    assert 'action="/u/dimos/follow"' in html
    assert ">Following<" in html


def test_logged_out_visitor_sees_neither_action(monkeypatch):
    _mock_profile_data(monkeypatch)
    client = dashboard.app.test_client()
    resp = client.get("/u/dimos")
    html = resp.data.decode()
    assert "Edit profile" not in html
    assert 'action="/u/dimos/follow"' not in html


# ── Profile sub-navigation: only real destinations ──

def test_profile_subnav_has_exactly_the_four_real_destinations(monkeypatch):
    _mock_profile_data(monkeypatch)
    client = dashboard.app.test_client()
    html = client.get("/u/dimos").data.decode()

    # Use the actual opening tag, not just the attribute name — the attribute
    # name alone also appears earlier in this template's own CSS comment.
    start = html.index('<nav class="wv-subnav" data-wv-scrollspy')
    end = html.index("</nav>", start)
    subnav = html[start:end]

    assert 'href="#overview"' in subnav
    assert 'href="#artists"' in subnav
    assert 'href="#activity"' in subnav
    assert 'href="/profile"' in subnav
    assert ">Overview<" in subnav
    assert ">Artists<" in subnav
    assert ">Activity<" in subnav
    assert ">Taste Profile<" in subnav

    # exactly these four links, no more
    assert subnav.count("<a ") == 4


def test_profile_page_has_no_dead_list_page_links(monkeypatch):
    _mock_profile_data(monkeypatch)
    client = dashboard.app.test_client()
    html = client.get("/u/dimos").data.decode()
    for dead in ("/followers", "/following", "/likes", "/messages"):
        assert dead not in html


def test_profile_sections_have_matching_anchor_ids(monkeypatch):
    _mock_profile_data(monkeypatch)
    client = dashboard.app.test_client()
    html = client.get("/u/dimos").data.decode()
    assert 'id="overview"' in html
    assert 'id="artists"' in html
    assert 'id="activity"' in html


# ── Stats are plain, not misleading links ──

def test_follow_stats_are_not_links(monkeypatch):
    _mock_profile_data(monkeypatch, followers=8, following=13)
    client = dashboard.app.test_client()
    html = client.get("/u/dimos").data.decode()
    # .pf-stats contains only <span> children (no nested <div>), so its own
    # closing </div> is the very next one after the opening tag.
    stats_start = html.index('class="pf-stats"')
    stats_end = html.index("</div>", stats_start)
    stats_block = html[stats_start:stats_end]
    assert "<a " not in stats_block
    assert "8" in stats_block
    assert "13" in stats_block


# ── Artists explored links resolve to the real artist route ──

def test_artists_explored_links_to_real_artist_route(monkeypatch):
    _mock_profile_data(monkeypatch, artists=["Radiohead", "Nujabes"])
    client = dashboard.app.test_client()
    html = client.get("/u/dimos").data.decode()
    assert 'href="/artist/Radiohead"' in html
    assert 'href="/artist/Nujabes"' in html


def test_artists_empty_state_preserved_for_own_profile(monkeypatch):
    _mock_profile_data(monkeypatch, artists=[])
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    html = client.get("/u/dimos").data.decode()
    assert "No artists explored yet" in html
    assert "Search an artist to start your taste graph" in html


# ── Activity/posts section still uses the shared post card, unchanged ──

def _post(id=5, username="alice", body="Loving this new album", user_id=2):
    return {
        "id": id, "body": body, "artist": "Radiohead",
        "created_at": datetime.datetime(2026, 7, 5, 12, 0),
        "user_id": user_id, "username": username,
        "like_count": 3, "comment_count": 1, "liked": False, "comments": [],
    }


def test_activity_section_renders_existing_post_card(monkeypatch):
    _mock_profile_data(monkeypatch, posts=[_post()])
    client = dashboard.app.test_client()
    html = client.get("/u/dimos").data.decode()
    assert "Loving this new album" in html
    assert 'action="/post/5/like"' in html


def test_posts_empty_state_preserved_for_own_profile(monkeypatch):
    _mock_profile_data(monkeypatch, posts=[])
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    html = client.get("/u/dimos").data.decode()
    assert "No posts yet" in html
    assert "Share a discovery with the community" in html


# ── Hero avatar: circular variant, still deterministic per username ──

def test_hero_avatar_uses_circular_variant(monkeypatch):
    _mock_profile_data(monkeypatch)
    client = dashboard.app.test_client()
    html = client.get("/u/dimos").data.decode()
    assert "wv-avatar-hero" in html
    assert "background:hsl(" in html

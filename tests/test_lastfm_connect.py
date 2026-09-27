"""Connect Last.fm → Taste profile from real listening (27 Sep 2026).
No network, database or AI calls."""

import pytest

import dashboard
import lastfm_user
import views_taste
from test_auth_navigation import _login


@pytest.fixture(autouse=True)
def _fresh():
    lastfm_user._cache.clear()
    views_taste._taste_cache.clear()
    yield
    lastfm_user._cache.clear()
    views_taste._taste_cache.clear()


class _Resp:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


# ── pure logic ──────────────────────────────────────────────────────

def test_username_rules():
    assert lastfm_user.USERNAME_RE.match("blakenor")
    assert lastfm_user.USERNAME_RE.match("Dimos_91-x")
    for bad in ("", "a", "1abc", "has space", "x" * 16, "<script>"):
        assert not lastfm_user.USERNAME_RE.match(bad)


def test_validate_username_rejects_bad_format_without_calling_lastfm(monkeypatch):
    monkeypatch.setattr(lastfm_user.http_requests, "get",
                        lambda *a, **k: pytest.fail("must not call Last.fm"))
    assert lastfm_user.validate_username("no spaces allowed") is None


def test_validate_username_returns_canonical_name(monkeypatch):
    monkeypatch.setattr(lastfm_user.http_requests, "get",
                        lambda *a, **k: _Resp({"user": {"name": "BlakeNor"}}))
    assert lastfm_user.validate_username("blakenor") == "BlakeNor"
    monkeypatch.setattr(lastfm_user.http_requests, "get",
                        lambda *a, **k: _Resp({"error": 6, "message": "User not found"}))
    assert lastfm_user.validate_username("nobodyhere") is None


def test_top_artists_parsed_and_cached(monkeypatch):
    calls = []

    def fake(*a, **k):
        calls.append(k["params"]["period"])
        return _Resp({"topartists": {"artist": [
            {"name": "Bonobo", "playcount": "120"}, {"name": "Burial", "playcount": "80"},
            {"playcount": "5"}]}})
    monkeypatch.setattr(lastfm_user.http_requests, "get", fake)
    top = lastfm_user.get_top_artists("blakenor", "7day")
    assert top == [{"name": "Bonobo", "plays": 120}, {"name": "Burial", "plays": 80}]
    lastfm_user.get_top_artists("BLAKENOR", "7day")
    assert calls == ["7day"]
    lastfm_user.get_top_artists("blakenor", "bogus")    # falls back to default period
    assert calls[-1] == lastfm_user.DEFAULT_PERIOD


def test_failed_top_artists_are_not_cached(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(lastfm_user.http_requests, "get", boom)
    assert lastfm_user.get_top_artists("blakenor") == []
    assert not any(k[0] == "top" for k in lastfm_user._cache)


def test_clean_tags_drops_noise():
    raw = [{"name": "electronic", "count": 100}, {"name": "seen live", "count": 90},
           {"name": "2010s", "count": 80}, {"name": "downtempo", "count": 60},
           {"name": "British", "count": 50}, {"name": "trip-hop", "count": 40}]
    assert lastfm_user.clean_tags(raw) == [("electronic", 100), ("downtempo", 60), ("trip-hop", 40)]


def test_genre_weights_follow_plays_and_sum_to_100():
    top = [{"name": "A", "plays": 300}, {"name": "B", "plays": 100}]
    tags = {"A": [("jazz", 100), ("soul", 50)], "B": [("soul", 100)]}
    g = lastfm_user.genre_weights(top, tags)
    assert [x["name"] for x in g] == ["jazz", "soul"]
    assert sum(x["share"] for x in g) == 100


def test_recommendations_skip_known_and_rank_by_votes():
    top = [{"name": "A", "plays": 9}, {"name": "B", "plays": 8}, {"name": "C", "plays": 7}]
    similar = {"A": ["X", "B", "Y"], "B": ["Y", "Z"], "C": ["Y", "a"]}
    assert lastfm_user.recommendations(top, similar) == ["Y", "X", "Z"]


# ── views ───────────────────────────────────────────────────────────

def test_connect_rejects_unknown_account(monkeypatch):
    monkeypatch.setattr(lastfm_user, "validate_username", lambda u: None)
    monkeypatch.setattr(lastfm_user, "set_link", lambda *a: pytest.fail("must not save"))
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.post("/profile/lastfm", data={"lastfm_username": "nope"})
    assert resp.status_code == 302 and "lastfm_error=1" in resp.headers["Location"]


def test_connect_saves_canonical_name(monkeypatch):
    saved = []
    monkeypatch.setattr(lastfm_user, "validate_username", lambda u: "BlakeNor")
    monkeypatch.setattr(lastfm_user, "set_link", lambda uid, name: saved.append((uid, name)))
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.post("/profile/lastfm", data={"lastfm_username": "blakenor"})
    assert resp.status_code == 302
    assert saved and saved[0][1] == "BlakeNor"


def test_connect_requires_login():
    resp = dashboard.app.test_client().post("/profile/lastfm", data={"lastfm_username": "x"})
    assert resp.status_code in (302, 401)


def test_connected_profile_shows_grid_sound_recs_and_card(monkeypatch):
    monkeypatch.setattr(lastfm_user, "get_link", lambda uid: "BlakeNor")
    monkeypatch.setattr(lastfm_user, "build_taste", lambda name, period: {
        "top": [{"name": "Bonobo", "plays": 1200}, {"name": "Burial", "plays": 800},
                {"name": "Four Tet", "plays": 500}],
        "genres": [{"name": "electronic", "share": 60}, {"name": "downtempo", "share": 40}],
        "recs": ["Floating Points"],
        "photos": {"Bonobo": {"image": "https://cdn-images.dzcdn.net/images/artist/a/250x250-000000-80-0-0.jpg"}},
    })
    monkeypatch.setattr(views_taste, "_listening_summary", lambda *a: "A calm, textured listener.")
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/profile?period=7day").data.decode()
    assert "Your taste, from what you play" in html
    assert "Connected to Last.fm as <b>BlakeNor</b>" in html
    assert 'href="?period=7day" aria-current="page"' in html
    assert "1.2K plays" in html and "/500x500-" in html
    assert "electronic" in html and "60%" in html
    assert "Floating Points" in html
    assert "A calm, textured listener." in html
    assert 'id="tp-card-data"' in html and "js/taste-card.js" in html


def test_unconnected_profile_offers_connect(monkeypatch):
    monkeypatch.setattr(lastfm_user, "get_link", lambda uid: None)

    class Cur:
        def execute(self, *a, **k):
            pass

        def fetchall(self):
            return []

    import contextlib

    @contextlib.contextmanager
    def cm(commit=False):
        yield Cur()
    monkeypatch.setattr(views_taste, "db_cursor", cm)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/profile").data.decode()
    assert 'action="/profile/lastfm"' in html and "Connect your listening" in html


def test_disconnect_removes_link(monkeypatch):
    removed = []
    monkeypatch.setattr(lastfm_user, "remove_link", lambda uid: removed.append(uid))
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.post("/profile/lastfm/disconnect")
    assert resp.status_code == 302 and removed


def test_taste_card_script_draws_only_same_origin_images():
    js = open("static/js/taste-card.js").read()
    assert "toBlob" in js and "data.mark" in js
    assert "dzcdn" not in js

"""Redesign phase 4: curated genre pages built from Last.fm tags.
No real network or database calls anywhere in this file.
"""

import pytest

import dashboard
import services
import views_genres


@pytest.fixture(autouse=True)
def _clear_caches():
    services._genre_cache.clear()
    services._artist_photo_cache.clear()
    yield
    services._genre_cache.clear()
    services._artist_photo_cache.clear()


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _fake_lastfm(calls=None, artists=("Massive Attack", "Portishead", "Tricky")):
    def get(url, params=None, **kw):
        if calls is not None:
            calls.append(params.get("method"))
        if params.get("method") == "tag.getInfo":
            return _Resp({"tag": {"wiki": {"summary":
                'Trip hop is a <b>genre</b> from Bristol &amp; beyond. '
                '<a href="https://www.last.fm/tag/trip-hop">Read more on Last.fm</a>'}}})
        if params.get("method") == "tag.getTopArtists":
            return _Resp({"topartists": {"artist": [{"name": n} for n in artists]}})
        raise AssertionError(f"unexpected call {params}")
    return get


# ── services ─────────────────────────────────────────────────────────

def test_unknown_slug_returns_none_without_any_network_call(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network called for an unknown genre")
    monkeypatch.setattr(services.http_requests, "get", boom)
    assert services.get_genre("anything-a-crawler-invents") is None


def test_genre_combines_description_and_top_artists(monkeypatch):
    monkeypatch.setattr(services.http_requests, "get", _fake_lastfm())
    g = services.get_genre("trip-hop")
    assert g["label"] == "Trip-hop"
    assert g["artists"] == ["Massive Attack", "Portishead", "Tricky"]
    assert g["summary"] == "Trip hop is a genre from Bristol & beyond."   # HTML + "Read more" stripped


def test_genre_is_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(services.http_requests, "get", _fake_lastfm(calls))
    services.get_genre("jazz")
    services.get_genre("jazz")
    assert calls.count("tag.getTopArtists") == 1


def test_genre_failure_never_raises_and_is_retried_sooner(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(services.http_requests, "get", boom)
    g = services.get_genre("soul")
    assert g["artists"] == [] and g["summary"] == ""
    assert services._genre_cache["soul"]["ok"] is False


def test_analysed_artist_names_never_raises_without_db():
    assert services.analysed_artist_names(["Massive Attack"]) == set()
    assert services.analysed_artist_names([]) == set()


# ── pages ────────────────────────────────────────────────────────────

def _stub_page(monkeypatch, artists=("Massive Attack", "Portishead"), analysed=("massive attack",)):
    monkeypatch.setattr(views_genres, "get_genre", lambda slug: None if slug not in services.GENRES_BY_SLUG else
                        dict(services.GENRES_BY_SLUG[slug], summary="About the sound.", artists=list(artists)))
    monkeypatch.setattr(views_genres, "get_artist_photos", lambda names: {
        "Massive Attack": {"image": "https://cdn-images.dzcdn.net/images/artist/ma/250x250-000000-80-0-0.jpg",
                           "nb_fan": 1_200_000}})
    monkeypatch.setattr(views_genres, "analysed_artist_names", lambda names: set(analysed))
    return dashboard.app.test_client()


def test_genre_page_renders_artists_photos_and_analysed_badge(monkeypatch):
    html = _stub_page(monkeypatch).get("/genre/trip-hop").data.decode()
    assert "Trip-hop" in html
    assert "About the sound." in html
    grid = html[html.index('class="gp-grid"'):html.index('class="gp-others"')]
    assert 'href="/artist/Massive%20Attack"' in grid and 'href="/artist/Portishead"' in grid
    assert "/ma/500x500-" in grid
    assert grid.count('class="gp-badge"') == 1          # only the analysed one
    assert "1.2M Deezer fans" in grid
    assert "<b>1</b> analysed on Waveline" in html


def test_unknown_genre_is_404(monkeypatch):
    resp = _stub_page(monkeypatch).get("/genre/not-a-genre")
    assert resp.status_code == 404


def test_other_genres_are_linked_but_not_the_current_one(monkeypatch):
    html = _stub_page(monkeypatch).get("/genre/jazz").data.decode()
    others = html[html.index('class="gp-others"'):]
    assert 'href="/genre/soul"' in others
    assert 'href="/genre/jazz"' not in others


def test_genre_page_empty_state_when_lastfm_is_down(monkeypatch):
    html = _stub_page(monkeypatch, artists=()).get("/genre/soul").data.decode()
    assert "Artists are temporarily unavailable" in html


def test_genres_index_lists_every_genre(monkeypatch):
    monkeypatch.setattr(views_genres, "get_genre_covers", lambda: [
        {"slug": g["slug"], "label": g["label"], "artist": "Lead", "image": ""} for g in services.GENRES])
    html = dashboard.app.test_client().get("/genres").data.decode()
    for g in services.GENRES:
        assert f'href="/genre/{g["slug"]}"' in html


def test_genres_link_in_primary_nav_and_homepage(monkeypatch):
    import views_main
    monkeypatch.setattr(views_main, "get_dashboard_data", lambda: (0, 0, 0, [], [], []))
    monkeypatch.setattr(views_main, "get_feed", lambda *a, **k: [])
    monkeypatch.setattr(views_main, "get_plays_analysed", lambda: None)
    html = dashboard.app.test_client().get("/").data.decode()
    nav = html[html.index('<nav class="wv-nav"'):html.index("</nav>", html.index('<nav class="wv-nav"'))]
    assert 'href="/genres"' in nav
    assert 'href="/genre/trip-hop"' in html

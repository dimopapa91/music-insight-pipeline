"""Redesign phase 2: homepage photos, the "Just analysed" strip and the
homepage KPIs. No real network or database calls anywhere in this file.
"""

import datetime

import pytest

import dashboard
import services
import views_main


@pytest.fixture(autouse=True)
def _clear_caches():
    services._artist_photo_cache.clear()
    services._site_pulse_cache.update(data=None, at=0.0)
    services._plays_total_cache.update(data=None, at=0.0)
    yield
    services._artist_photo_cache.clear()
    services._site_pulse_cache.update(data=None, at=0.0)
    services._plays_total_cache.update(data=None, at=0.0)


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _deezer(*artists):
    return _Resp({"data": [
        {"name": n, "nb_fan": fans, "picture_medium":
            f"https://cdn-images.dzcdn.net/images/artist/{h}/250x250-000000-80-0-0.jpg"}
        for n, fans, h in artists
    ]})


# ── get_artist_photo: pick the right namesake ────────────────────────

def test_photo_prefers_the_most_followed_exact_name_match(monkeypatch):
    # Deezer's first result for "Bonobo" is a tiny namesake (real case).
    monkeypatch.setattr(services.http_requests, "get", lambda *a, **k: _deezer(
        ("Bonobo", 63, "aaa"), ("Bonobo", 365253, "bbb"), ("Bonobo Kids", 900000, "ccc")))
    photo = services.get_artist_photo("Bonobo")
    assert "/bbb/" in photo["image"]
    assert photo["nb_fan"] == 365253


def test_photo_rejects_results_whose_name_does_not_match(monkeypatch):
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: _deezer(("Blake Shelton", 5_000_000, "zzz")))
    assert services.get_artist_photo("BlakeNor") == {"image": "", "nb_fan": 0}


def test_photo_drops_deezer_blank_placeholder(monkeypatch):
    monkeypatch.setattr(services.http_requests, "get", lambda *a, **k: _deezer(
        ("Hird", 1422, services.DEEZER_BLANK_IMAGE_HASH)))
    assert services.get_artist_photo("Hird")["image"] == ""


def test_photo_is_cached(monkeypatch):
    calls = []

    def fake_get(*a, **k):
        calls.append(1)
        return _deezer(("Tricky", 141201, "ttt"))
    monkeypatch.setattr(services.http_requests, "get", fake_get)
    services.get_artist_photo("Tricky")
    services.get_artist_photo("tricky")
    assert len(calls) == 1


def test_photo_failure_returns_empty_and_never_raises(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(services.http_requests, "get", boom)
    assert services.get_artist_photo("Portishead") == {"image": "", "nb_fan": 0}


def test_get_artist_photos_maps_every_name(monkeypatch):
    monkeypatch.setattr(services, "get_artist_photo", lambda n: {"image": n.lower(), "nb_fan": 1})
    assert services.get_artist_photos(["A", "B"]) == {
        "A": {"image": "a", "nb_fan": 1}, "B": {"image": "b", "nb_fan": 1}}
    assert services.get_artist_photos([]) == {}


# ── deezer_image_size ────────────────────────────────────────────────

def test_deezer_image_size_rewrites_the_square_size():
    url = "https://cdn-images.dzcdn.net/images/artist/abc/250x250-000000-80-0-0.jpg"
    assert services.deezer_image_size(url, 1000) == \
        "https://cdn-images.dzcdn.net/images/artist/abc/1000x1000-000000-80-0-0.jpg"


def test_deezer_image_size_passes_other_urls_through():
    assert services.deezer_image_size("", 500) == ""
    other = "https://i.scdn.co/image/ab67616d0000b273"
    assert services.deezer_image_size(other, 500) == other


# ── get_site_pulse ───────────────────────────────────────────────────

def test_site_pulse_never_raises_without_a_database():
    # conftest pins DATABASE_URL to an unreachable address.
    assert services.get_site_pulse() == {"recent": []}


def test_plays_analysed_never_raises_without_a_database():
    assert services.get_plays_analysed() is None


@pytest.mark.parametrize("n,text", [
    (4_213_000_000, "4.2B"), (193_600_000, "193.6M"), (1_000_000, "1M"),
    (12_400, "12.4K"), (999, "999"), (None, "—"), ("x", "—"),
])
def test_compact_number(n, text):
    assert services.compact_number(n) == text


# ── rendering ────────────────────────────────────────────────────────

class _Row:
    def __init__(self, artist, photo=""):
        self.artist = artist
        self.photo = photo
        self.insight = "An insight."
        self.searched_at = datetime.datetime.utcnow() - datetime.timedelta(hours=2)
        self.top_tracks = ["Teardrop"]
        self.similar_artists = []


def _home(monkeypatch, rows, pulse, plays=None):
    monkeypatch.setattr(views_main, "get_dashboard_data", lambda: (10, 7, 0, [], rows, []))
    monkeypatch.setattr(views_main, "get_plays_analysed", lambda: plays)
    monkeypatch.setattr(views_main, "get_feed", lambda *a, **k: [])
    monkeypatch.setattr(dashboard, "get_site_pulse", lambda: pulse)
    return dashboard.app.test_client().get("/").data.decode()


def test_hero_features_latest_artist_photo_at_full_size(monkeypatch):
    photo = "https://cdn-images.dzcdn.net/images/artist/abc/250x250-000000-80-0-0.jpg"
    html = _home(monkeypatch, [_Row("Massive Attack", photo)], {"recent": [], })
    hero = html[html.index('id="top"'):html.index('id="wv-title"')]
    assert "/abc/1000x1000-" in hero
    assert "Massive Attack" in hero
    assert 'href="/artist/Massive%20Attack"' in hero


def test_hero_without_photo_falls_back_to_initial(monkeypatch):
    html = _home(monkeypatch, [_Row("Massive Attack")], {"recent": [], })
    hero = html[html.index('id="top"'):html.index('id="wv-title"')]
    assert "wv-feature-noimg" in hero
    assert "<img" not in hero


def test_hero_without_any_artist_is_search_only(monkeypatch):
    html = _home(monkeypatch, [], {"recent": [], })
    assert 'class="wv-hero no-feature"' in html
    assert "wv-feature" not in html[html.index('id="top"'):html.index('id="wv-title"')]


def test_kpis_show_total_plays_compact_and_no_search_counts(monkeypatch):
    html = _home(monkeypatch, [], {"recent": []}, plays=4_213_000_000)
    kpis = html[html.index('class="wv-kpis"'):html.index('id="chapter-01"')]
    assert "4.2B" in kpis
    assert "Plays analysed" in kpis
    assert "Members" not in kpis
    assert "Searches" not in kpis   # bot-inflated search counts stay off the homepage


def test_kpis_show_dash_when_plays_unknown(monkeypatch):
    html = _home(monkeypatch, [], {"recent": [], })
    kpis = html[html.index('class="wv-kpis"'):html.index('id="chapter-01"')]
    assert "—" in kpis


def test_just_analysed_strip_lists_recent_artists(monkeypatch):
    now = datetime.datetime.utcnow()
    pulse = {"recent": [{"artist": "Massive Attack", "at": now - datetime.timedelta(hours=2)},
                        {"artist": "Nicolas Jaar", "at": now - datetime.timedelta(minutes=5)}],
             }
    html = _home(monkeypatch, [], pulse)
    strip = html[html.index('class="wv-pulse"'):html.index("</nav>", html.index('class="wv-pulse"'))]
    assert "Just analysed" in strip
    assert 'href="/artist/Massive%20Attack"' in strip
    assert "2h ago" in strip
    assert 'href="/artist/Nicolas%20Jaar"' in strip


def test_just_analysed_strip_hidden_when_nothing_recent(monkeypatch):
    html = _home(monkeypatch, [], {"recent": [], })
    assert 'class="wv-pulse"' not in html

"""Tests for the Spotify quota fix and the Deezer new-releases switch.

Two production problems, both verified against live credentials:
- /v1/search answers 429 QUOTA_EXCEEDED because the app sits in Spotify's
  Development Mode and get_spotify_artist() ran uncached on every artist
  page view. The fix caches by name, including negative results, so a burst
  of misses during quota exhaustion stops re-hitting Spotify.
- /v1/browse/new-releases answers 403 permanently for this app tier, so the
  news page's strip comes from Deezer instead — specifically the keyless
  albums chart, since Deezer's /editorial/0/releases turned out to answer
  200 with an empty data list and rendered blank.

No real network calls anywhere in this file.
"""

import pytest

import services


@pytest.fixture(autouse=True)
def _clear_services_caches():
    """These caches are module-level and would otherwise leak between tests
    (and into the rest of the suite, which shares one imported services)."""
    services._spotify_artist_cache.clear()
    services._spotify_token_cache.clear()
    yield
    services._spotify_artist_cache.clear()
    services._spotify_token_cache.clear()


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


def _artist_payload():
    return {
        "artists": {
            "items": [{
                # Must match the queried name: get_spotify_artist() now
                # rejects a top result whose name doesn't (see
                # tests/test_artist_image_matching.py).
                "name": "Radiohead",
                "popularity": 82,
                "followers": {"total": 1234},
                "genres": ["art rock", "alternative", "indie", "britpop", "extra"],
                "external_urls": {"spotify": "https://open.spotify.com/artist/abc"},
                "images": [{"url": "big.jpg"}, {"url": "medium.jpg"}],
            }]
        }
    }


# ── get_spotify_artist: quota (429) + caching ────────────────────────

def test_quota_exceeded_returns_empty_and_is_cached_without_a_second_request(monkeypatch):
    monkeypatch.setattr(services, "get_spotify_token", lambda: "fake-token")
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(url)
        return _FakeResponse(429, text='{"reason":"QUOTA_EXCEEDED"}')

    monkeypatch.setattr(services.http_requests, "get", fake_get)

    assert services.get_spotify_artist("Radiohead") == {}
    assert len(calls) == 1

    # The negative cache is the actual quota saver: a second view of the same
    # artist during the outage must not spend another request.
    assert services.get_spotify_artist("Radiohead") == {}
    assert len(calls) == 1


def test_successful_lookup_is_cached_for_repeat_views(monkeypatch):
    monkeypatch.setattr(services, "get_spotify_token", lambda: "fake-token")
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(url)
        return _FakeResponse(200, _artist_payload())

    monkeypatch.setattr(services.http_requests, "get", fake_get)

    first = services.get_spotify_artist("Radiohead")
    second = services.get_spotify_artist("Radiohead")

    assert len(calls) == 1
    assert first == second
    # Unchanged dict shape when Spotify works.
    assert first == {
        "popularity": 82,
        "followers": 1234,
        "genres": ["art rock", "alternative", "indie", "britpop"],
        "spotify_url": "https://open.spotify.com/artist/abc",
        "image": "medium.jpg",
    }


def test_cache_key_is_normalised_across_case_and_whitespace(monkeypatch):
    monkeypatch.setattr(services, "get_spotify_token", lambda: "fake-token")
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(url)
        return _FakeResponse(200, _artist_payload())

    monkeypatch.setattr(services.http_requests, "get", fake_get)

    services.get_spotify_artist("Radiohead")
    services.get_spotify_artist("  radiohead  ")
    assert len(calls) == 1


def test_negative_cache_expires_so_spotify_is_retried_later(monkeypatch):
    monkeypatch.setattr(services, "get_spotify_token", lambda: "fake-token")
    calls = []
    responses = [_FakeResponse(429), _FakeResponse(200, _artist_payload())]

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(url)
        return responses[len(calls) - 1]

    monkeypatch.setattr(services.http_requests, "get", fake_get)

    assert services.get_spotify_artist("Radiohead") == {}
    # Age the negative entry past its (shorter) TTL — the quota is meant to
    # recover, so this must not be a permanent blackout.
    # A 429 pauses Spotify (honouring Retry-After) instead of caching a
    # blank; once the pause is over it is asked again.
    assert services.photo_store.paused("spotify")
    assert services.get_spotify_artist("Radiohead") == {} and len(calls) == 1   # paused: no request
    services.photo_store.reset_pauses()

    assert services.get_spotify_artist("Radiohead")["popularity"] == 82
    assert len(calls) == 2


def test_quota_and_generic_errors_are_logged_distinctly(monkeypatch, caplog):
    monkeypatch.setattr(services, "get_spotify_token", lambda: "fake-token")
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: _FakeResponse(429))
    with caplog.at_level("WARNING"):
        services.get_spotify_artist("Radiohead")
    assert "quota exceeded (429)" in caplog.text

    caplog.clear()
    services._spotify_artist_cache.clear()
    services.photo_store.reset_pauses()
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: _FakeResponse(503))
    with caplog.at_level("WARNING"):
        services.get_spotify_artist("Radiohead")
    assert "HTTP 503" in caplog.text
    assert "quota exceeded" not in caplog.text
    # The bearer token must never reach the logs.
    assert "fake-token" not in caplog.text


def test_missing_token_is_cached_and_makes_no_request(monkeypatch):
    monkeypatch.setattr(services, "get_spotify_token", lambda: None)
    calls = []
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: calls.append(1) or _FakeResponse(200))

    assert services.get_spotify_artist("Radiohead") == {}
    assert services.get_spotify_artist("Radiohead") == {}
    assert calls == []


def test_exception_is_cached_as_a_failure(monkeypatch):
    monkeypatch.setattr(services, "get_spotify_token", lambda: "fake-token")
    calls = []

    def _boom(*a, **k):
        calls.append(1)
        raise ConnectionError("network down")

    monkeypatch.setattr(services.http_requests, "get", _boom)

    assert services.get_spotify_artist("Radiohead") == {}
    assert services.get_spotify_artist("Radiohead") == {}
    assert len(calls) == 1


# ── get_deezer_trending_albums ───────────────────────────────────────

def _deezer_payload():
    """Shaped like a real /chart/0/albums response: no release_date field,
    which is exactly the difference from the dead /editorial/0/releases."""
    return {
        "data": [
            {
                "title": "In Rainbows",
                "artist": {"name": "Radiohead"},
                "cover_medium": "https://cdn.deezer.com/cover/medium.jpg",
                "link": "https://www.deezer.com/album/111",
                "record_type": "album",
            },
            {
                "title": "A Single",
                "artist": {"name": "SZA"},
                "cover_medium": "https://cdn.deezer.com/cover/single.jpg",
                "link": "https://www.deezer.com/album/222",
                "record_type": "single",
            },
        ]
    }


def test_deezer_albums_map_to_the_template_shape(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(200, _deezer_payload())

    monkeypatch.setattr(services.http_requests, "get", fake_get)

    releases = services.get_deezer_trending_albums()

    assert captured["url"] == "https://api.deezer.com/chart/0/albums"
    assert captured["params"] == {"limit": 12}
    assert releases == [
        {
            "name": "In Rainbows",
            "artist": "Radiohead",
            "image": "https://cdn.deezer.com/cover/medium.jpg",
            "url": "https://www.deezer.com/album/111",
            "type": "Album",
            "date": "",
        },
        {
            "name": "A Single",
            "artist": "SZA",
            "image": "https://cdn.deezer.com/cover/single.jpg",
            "url": "https://www.deezer.com/album/222",
            "type": "Single",
            "date": "",
        },
    ]
    # Chart albums carry no release_date: the missing field maps to "" rather
    # than exploding, and the template omits an empty date.
    assert all(r["date"] == "" for r in releases)
    # Exactly the keys templates/news.html renders — no more, no less.
    assert set(releases[0]) == {"name", "artist", "image", "url", "type", "date"}


def test_deezer_albums_non_200_returns_empty_list(monkeypatch):
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: _FakeResponse(503, text="upstream error"))
    assert services.get_deezer_trending_albums() == []


def test_deezer_albums_exception_returns_empty_list(monkeypatch):
    def _boom(*a, **k):
        raise ConnectionError("network down")

    monkeypatch.setattr(services.http_requests, "get", _boom)
    assert services.get_deezer_trending_albums() == []


def test_deezer_albums_empty_data_returns_empty_list(monkeypatch):
    # What /editorial/0/releases actually did: HTTP 200 with no rows. The
    # strip must read as "unavailable", not silently render nothing.
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: _FakeResponse(200, {"data": [], "total": 0}))
    assert services.get_deezer_trending_albums() == []


def test_news_data_now_comes_from_independent_publications(monkeypatch):
    # 27 Sep 2026: /news moved to scene-focused independent outlets; the
    # Deezer chart is no longer part of it (get_deezer_trending_albums stays
    # available for other callers).
    import news_feeds
    monkeypatch.setattr(news_feeds, "fetch_feed", lambda feed: [])
    services.clear_news_cache()
    data = services.get_news_data()
    names = [s["name"] for s in data["sources"]]
    assert "Deezer" not in names and "NME" not in names and "Pitchfork" not in names
    assert "The Quietus" in names and "Bandcamp Daily" in names
    services.clear_news_cache()

"""Tests for the Spotify quota fix and the Deezer new-releases switch.

Two production problems, both verified against live credentials:
- /v1/search answers 429 QUOTA_EXCEEDED because the app sits in Spotify's
  Development Mode and get_spotify_artist() ran uncached on every artist
  page view. The fix caches by name, including negative results, so a burst
  of misses during quota exhaustion stops re-hitting Spotify.
- /v1/browse/new-releases answers 403 permanently for this app tier, so the
  news page's releases strip now comes from Deezer's keyless editorial
  endpoint instead.

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
    services._spotify_artist_cache["radiohead"]["at"] -= services._SPOTIFY_ARTIST_NEG_TTL + 1

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


# ── get_deezer_new_releases ──────────────────────────────────────────

def _deezer_payload():
    return {
        "data": [
            {
                "title": "In Rainbows",
                "artist": {"name": "Radiohead"},
                "cover_medium": "https://cdn.deezer.com/cover/medium.jpg",
                "link": "https://www.deezer.com/album/111",
                "record_type": "album",
                "release_date": "2026-09-11",
            },
            {
                "title": "A Single",
                "artist": {"name": "SZA"},
                "cover_medium": "https://cdn.deezer.com/cover/single.jpg",
                "link": "https://www.deezer.com/album/222",
                "record_type": "single",
                "release_date": "2026-09-12",
            },
        ]
    }


def test_deezer_releases_map_to_the_template_shape(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(200, _deezer_payload())

    monkeypatch.setattr(services.http_requests, "get", fake_get)

    releases = services.get_deezer_new_releases()

    assert captured["url"] == "https://api.deezer.com/editorial/0/releases"
    assert captured["params"] == {"limit": 12}
    assert releases == [
        {
            "name": "In Rainbows",
            "artist": "Radiohead",
            "image": "https://cdn.deezer.com/cover/medium.jpg",
            "url": "https://www.deezer.com/album/111",
            "type": "Album",
            "date": "2026-09-11",
        },
        {
            "name": "A Single",
            "artist": "SZA",
            "image": "https://cdn.deezer.com/cover/single.jpg",
            "url": "https://www.deezer.com/album/222",
            "type": "Single",
            "date": "2026-09-12",
        },
    ]
    # Exactly the keys templates/news.html renders — no more, no less.
    assert set(releases[0]) == {"name", "artist", "image", "url", "type", "date"}


def test_deezer_releases_non_200_returns_empty_list(monkeypatch):
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: _FakeResponse(503, text="upstream error"))
    assert services.get_deezer_new_releases() == []


def test_deezer_releases_exception_returns_empty_list(monkeypatch):
    def _boom(*a, **k):
        raise ConnectionError("network down")

    monkeypatch.setattr(services.http_requests, "get", _boom)
    assert services.get_deezer_new_releases() == []


def test_news_data_uses_deezer_and_lists_it_as_a_source(monkeypatch):
    monkeypatch.setattr(services, "fetch_rss", lambda feed: [])
    monkeypatch.setattr(services, "get_deezer_new_releases",
                        lambda: [{"name": "X", "artist": "Y", "image": "",
                                   "url": "", "type": "Album", "date": ""}])
    services.clear_news_cache()
    monkeypatch.setattr(services, "_last_releases", [])

    data = services.get_news_data()

    assert data["releases_status"] == "live"
    assert data["sources"][-1] == "Deezer"
    assert "Spotify" not in data["sources"]
    services.clear_news_cache()

"""Tests for provider-result name matching.

Reported bug: a niche artist ("BlakeNor") showed some unrelated popular
artist's photo, genres and popularity. Cause: Spotify and Deezer both answer
a miss with their nearest popular match rather than nothing, and both call
sites took items[0] without checking the returned name. artist_names_match()
gates that now.

No real network or database calls anywhere in this file.
"""

import pytest

import services


@pytest.fixture(autouse=True)
def _clear_spotify_caches():
    """Module-level caches, cleared so they can't leak between tests (or
    into the rest of the suite) — same pattern as the other services tests."""
    services._spotify_artist_cache.clear()
    services._spotify_token_cache.clear()
    yield
    services._spotify_artist_cache.clear()
    services._spotify_token_cache.clear()


# ── artist_names_match ───────────────────────────────────────────────

@pytest.mark.parametrize("query,result", [
    ("Radiohead", "Radiohead"),
    ("radiohead", "RADIOHEAD"),           # case
    ("  Radiohead  ", "Radiohead"),       # whitespace
    ("Blake Nor", "BlakeNor"),            # internal spacing
    ("BlakeNor", "Blake Nor"),            # ...and the reverse
    ("Beyoncé", "beyonce"),               # accents
    ("Sigur Rós", "Sigur Ros"),
    ("Tyler, The Creator", "Tyler The Creator"),   # punctuation
    ("AC/DC", "ACDC"),
])
def test_names_that_should_match(query, result):
    assert services.artist_names_match(query, result) is True


@pytest.mark.parametrize("query,result", [
    ("BlakeNor", "Blake Shelton"),        # the actual reported bug
    ("Radiohead", "Radio Company"),
    ("BlakeNor", "Blake"),                # prefix is not a match
    ("Blake", "BlakeNor"),                # nor is being a prefix
    ("", "Blake Shelton"),                # empty query never matches
    ("BlakeNor", ""),                     # nor an empty result
    ("BlakeNor", None),
    (None, "BlakeNor"),
])
def test_names_that_should_not_match(query, result):
    assert services.artist_names_match(query, result) is False


def test_punctuation_only_query_does_not_match_everything():
    # Normalising strips punctuation, so a punctuation-only query collapses
    # to "" — it must not then equal another collapsed-empty string.
    assert services.artist_names_match("!!!", "???") is False


# ── get_spotify_artist name gating ───────────────────────────────────

class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _payload(name):
    return {"artists": {"items": [{
        "name": name,
        "popularity": 91,
        "followers": {"total": 5_000_000},
        "genres": ["country"],
        "external_urls": {"spotify": "https://open.spotify.com/artist/wrong"},
        "images": [{"url": "big.jpg"}, {"url": "medium.jpg"}],
    }]}}


def test_spotify_result_with_a_mismatched_name_is_rejected(monkeypatch):
    monkeypatch.setattr(services, "get_spotify_token", lambda: "fake-token")
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: _FakeResponse(_payload("Blake Shelton")))

    # Nothing from the wrong artist leaks through — not the photo, and not
    # the popularity/genres that made the page look confidently wrong.
    assert services.get_spotify_artist("BlakeNor") == {}


def test_spotify_result_with_a_matching_name_is_used(monkeypatch):
    monkeypatch.setattr(services, "get_spotify_token", lambda: "fake-token")
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: _FakeResponse(_payload("BlakeNor")))

    assert services.get_spotify_artist("BlakeNor") == {
        "popularity": 91,
        "followers": 5_000_000,
        "genres": ["country"],
        "spotify_url": "https://open.spotify.com/artist/wrong",
        "image": "medium.jpg",
    }


def test_spotify_match_tolerates_case_and_accent_differences(monkeypatch):
    monkeypatch.setattr(services, "get_spotify_token", lambda: "fake-token")
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: _FakeResponse(_payload("Beyoncé")))
    assert services.get_spotify_artist("beyonce")["popularity"] == 91


def test_rejected_match_is_cached_like_any_other_miss(monkeypatch):
    monkeypatch.setattr(services, "get_spotify_token", lambda: "fake-token")
    calls = []
    monkeypatch.setattr(services.http_requests, "get",
                        lambda *a, **k: calls.append(1) or _FakeResponse(_payload("Blake Shelton")))

    assert services.get_spotify_artist("BlakeNor") == {}
    assert services.get_spotify_artist("BlakeNor") == {}
    # Cached as a miss, so a mismatch doesn't re-spend the Spotify quota on
    # every page view.
    assert len(calls) == 1

"""Artist photos survive providers refusing the server (29 Sep 2026).

Deezer started answering the server with 403 and Spotify with 429 while bots
crawled artist pages, and every photo on the site vanished. Photos are now
stored in Postgres, providers are paused when they refuse us, and the page
asks Deezer from the visitor's browser for anything still missing.
"""

import photo_store
import services


class _Resp:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._payload


def _clear():
    services._artist_photo_cache.clear()
    services._deezer_card_cache.clear()


def test_deezer_403_pauses_deezer_and_serves_the_stored_photo(monkeypatch):
    _clear()
    monkeypatch.setattr(photo_store, "load", lambda name: {"image": "https://cdn-images.dzcdn.net/images/artist/abc/250x250-x.jpg", "nb_fan": 9, "fresh": False})
    monkeypatch.setattr(photo_store, "save", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(services.http_requests, "get", lambda *a, **k: calls.append(1) or _Resp(403, {}))
    assert "/abc/" in services.get_artist_photo("Son Lux")["image"]
    assert photo_store.paused("deezer")
    services._artist_photo_cache.clear()
    services.get_artist_photo("Son Lux")
    assert len(calls) == 1                                   # paused: no second request


def test_fresh_stored_photo_needs_no_deezer_call(monkeypatch):
    _clear()
    monkeypatch.setattr(photo_store, "load", lambda name: {"image": "https://cdn-images.dzcdn.net/images/artist/fff/250x250-x.jpg", "nb_fan": 3, "fresh": True})
    monkeypatch.setattr(services.http_requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call")))
    assert "/fff/" in services.get_artist_photo("Narrow Head")["image"]


def test_found_photos_are_saved(monkeypatch):
    _clear()
    saved = []
    monkeypatch.setattr(photo_store, "load", lambda name: None)
    monkeypatch.setattr(photo_store, "save", lambda *a, **k: saved.append(a))
    monkeypatch.setattr(services.http_requests, "get", lambda *a, **k: _Resp(200, {"data": [
        {"name": "Sam Evian", "nb_fan": 100, "picture_medium": "https://cdn-images.dzcdn.net/images/artist/s1/250x250-000000-80-0-0.jpg"}]}))
    services.get_artist_photo("Sam Evian")
    assert saved and saved[0][0] == "Sam Evian" and "/s1/" in saved[0][1]


def test_spotify_429_pauses_spotify_with_retry_after(monkeypatch):
    services._spotify_artist_cache.clear()
    monkeypatch.setattr(services, "get_spotify_token", lambda: "t")
    calls = []
    monkeypatch.setattr(services.http_requests, "get", lambda *a, **k: calls.append(1) or _Resp(429, {}, {"Retry-After": "120"}))
    assert services.get_spotify_artist("Aitana") == {}
    assert services.get_spotify_artist("Kaktov") == {}
    assert len(calls) == 1 and photo_store.paused("spotify")


def test_save_never_stores_blanks_or_non_https():
    photo_store.save("x", "")            # returns before touching the database
    photo_store.save("x", "http://insecure/img.jpg")


def test_placeholders_carry_the_artist_name_for_the_browser_fallback():
    for tpl, needle in (("templates/index.html", 'data-wv-photo="{{ featured.artist }}"'),
                        ("templates/index.html", 'data-wv-photo="{{ row.artist }}"'),
                        ("templates/artist_profile.html", 'data-wv-photo="{{ artist_name }}"'),
                        ("templates/artist_profile.html", 'data-wv-photo="{{ name }}"'),
                        ("templates/genre.html", 'data-wv-photo="{{ a.name }}"')):
        assert needle in open(tpl).read(), (tpl, needle)
    base = open("templates/base.html").read()
    assert "js/photo-fallback.js" in base


def test_csp_allows_deezer_jsonp_only_for_scripts():
    import site_meta
    csp = site_meta.SECURITY_HEADERS["Content-Security-Policy"]
    assert "script-src 'self' 'unsafe-inline' https://api.deezer.com" in csp
    assert "connect-src 'self'" in csp


def test_fallback_script_matches_names_exactly_and_skips_blank_images():
    js = open("static/js/photo-fallback.js").read()
    assert "norm(d.name) !== key" in js
    assert "d41d8cd98f00b204e9800998ecf8427e" in js
    assert "output=jsonp" in js

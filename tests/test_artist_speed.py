"""Artist page speed (27 Sep 2026): Last.fm artist.getInfo is cached, and
the external lookups run in parallel instead of one after another."""

import contextlib
import datetime
import json
import time

import dashboard
import views_artist


class _Lastfm:
    def __init__(self, listeners=100):
        self.listeners = listeners

    def json(self):
        return {"artist": {"stats": {"listeners": str(self.listeners), "playcount": "5"},
                           "tags": {"tag": [{"name": "trip-hop"}]}, "mbid": "abc"}}


def _stub(monkeypatch, delay=0.0, calls=None):
    row = ("Massive Attack", "An insight.", datetime.datetime(2026, 9, 2),
           json.dumps([{"name": "Teardrop", "playcount": "5"}]))

    class FakeCur:
        def __init__(self):
            self.n = 0

        def execute(self, sql, params=None):
            self.n += 1

        def fetchone(self):
            return row if self.n == 1 else (1,)

    @contextlib.contextmanager
    def fake_cm(commit=False):
        yield FakeCur()

    calls = calls if calls is not None else []

    def slow(result, label):
        def fn(*a, **k):
            calls.append(label)
            time.sleep(delay)
            return result
        return fn

    monkeypatch.setattr(views_artist, "db_cursor", fake_cm)
    monkeypatch.setattr(views_artist, "resolve_insight", lambda n, i: (i, False))
    monkeypatch.setattr(views_artist, "get_similar_artists", slow(["Portishead"], "similar"))
    monkeypatch.setattr(views_artist, "get_artist_events", slow([], "events"))
    monkeypatch.setattr(views_artist, "get_artist_media",
                        slow({"spotify": {}, "deezer_image": "", "deezer_fans": 0}, "media"))
    monkeypatch.setattr(views_artist, "get_artist_photos", slow({}, "photos"))
    monkeypatch.setattr(views_artist.http_requests, "get", slow(_Lastfm(), "lastfm"))
    return calls


def test_lastfm_info_is_cached_between_views(monkeypatch):
    calls = _stub(monkeypatch)
    client = dashboard.app.test_client()
    assert client.get("/artist/Massive%20Attack").status_code == 200
    assert client.get("/artist/Massive%20Attack").status_code == 200
    assert calls.count("lastfm") == 1


def test_failed_lastfm_info_is_retried_after_short_ttl(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(views_artist.http_requests, "get", boom)
    assert views_artist._lastfm_artist_info("X")["listeners"] == 0
    entry = views_artist._lastfm_info_cache["x"]
    assert entry["ok"] is False
    entry["at"] -= views_artist._LASTFM_INFO_NEG_TTL + 1
    monkeypatch.setattr(views_artist.http_requests, "get", lambda *a, **k: _Lastfm(42))
    assert views_artist._lastfm_artist_info("X")["listeners"] == 42


def test_external_lookups_run_in_parallel(monkeypatch):
    # 5 lookups at 0.3s each: ~1.5s in series, ~0.6s in two parallel waves.
    _stub(monkeypatch, delay=0.3)
    start = time.perf_counter()
    resp = dashboard.app.test_client().get("/artist/Massive%20Attack")
    elapsed = time.perf_counter() - start
    assert resp.status_code == 200
    assert elapsed < 1.1, elapsed


# ── perceived speed: progress bar + safe hover prefetch ─────────────

def _base():
    return open("templates/base.html").read()


def test_progress_bar_is_on_every_page_and_script_loaded():
    base = _base()
    assert 'id="wv-progress"' in base
    assert "js/nav-progress.js" in base
    js = open("static/js/nav-progress.js").read()
    assert "pageshow" in js            # cleared on back/forward cache restores
    assert "location.origin" in js     # only same-site navigations


def test_prefetch_rules_are_valid_json_and_exclude_side_effect_pages():
    import json as _json
    import re as _re
    base = _base()
    raw = _re.search(r'<script type="speculationrules">(.*?)</script>', base, _re.S).group(1)
    rules = _json.loads(raw)
    rule = rules["prefetch"][0]
    assert rule["eagerness"] == "moderate"
    text = _json.dumps(rule)
    assert '"href_matches": "/profile*"' in text   # taste profile may start an AI call
    selectors = rule["where"]["and"][0]["selector_matches"]
    # opt-in whitelist only: header nav, analysed-artist cards, genre tiles
    assert set(s.strip() for s in selectors.split(",")) == {
        ".wv-nav a", ".wv-feature", ".wv-fresh-card", ".wv-pulse-ch", ".gn-tile", ".wv-brand"}
    assert "gp-card" not in selectors   # genre artists may be unanalysed: never prefetch


def test_single_gunicorn_process_so_caches_are_shared():
    proc = open("Procfile").read()
    assert "--workers 1" in proc and "--threads 8" in proc

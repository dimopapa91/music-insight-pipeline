"""Opening an already-analysed artist from a search counts as the latest
search: the homepage and the "Just analysed" strip follow it, without a paid
pipeline re-run. No network or real database."""

import contextlib
import datetime
import json

import dashboard
import services
import views_artist


def _render(monkeypatch, query=""):
    row = ("Robert Glasper", "An insight.", datetime.datetime(2026, 9, 2), json.dumps([{"name": "Afro Blue", "playcount": "5"}]))

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

    class _Lastfm:
        status_code = 200

        def json(self):
            return {"artist": {"stats": {}, "tags": {"tag": []}, "mbid": ""}}

    opened = []
    monkeypatch.setattr(views_artist, "db_cursor", fake_cm)
    monkeypatch.setattr(views_artist, "resolve_insight", lambda n, i: (i, False))
    monkeypatch.setattr(views_artist, "get_similar_artists", lambda n: [])
    monkeypatch.setattr(views_artist, "get_artist_photos", lambda names: {})
    monkeypatch.setattr(views_artist, "get_artist_media", lambda n, m=None: {"spotify": {}, "deezer_image": "", "deezer_fans": 0})
    monkeypatch.setattr(views_artist, "get_artist_events", lambda n: [])
    monkeypatch.setattr(views_artist.http_requests, "get", lambda *a, **k: _Lastfm())
    monkeypatch.setattr(views_artist, "record_artist_open", lambda name, uid=None: opened.append(name))
    resp = dashboard.app.test_client().get("/artist/Robert%20Glasper" + query)
    return resp, opened


def test_opening_from_a_search_is_recorded(monkeypatch):
    resp, opened = _render(monkeypatch, "?from=search")
    assert resp.status_code == 200
    assert opened == ["Robert Glasper"]          # the stored name, not the URL text


def test_a_plain_visit_is_not_recorded(monkeypatch):
    _, opened = _render(monkeypatch)
    assert opened == []


def test_record_artist_open_never_raises_without_a_database():
    assert services.record_artist_open("Robert Glasper") is False


def test_freshness_marker_also_tracks_opens():
    # conftest's DB is unreachable: the marker is None and callers fall back to TTL.
    assert services.latest_search_id() is None
    sql = services._LATEST_ACTIVITY_SQL
    assert "artist_opens" in sql and "searches" in sql and "MAX(last_at)" in sql


def test_palette_and_homepage_suggestions_open_with_from_search():
    with open("static/js/command-palette.js") as f:
        palette = f.read()
    assert '"?from=search"' in palette
    with open("templates/index.html") as f:
        home = f.read()
    assert "?from=search" in home
    # choosing an existing artist from the suggestions no longer re-runs the pipeline
    block = home[home.index("div.addEventListener('mousedown'"):]
    block = block[:block.index("\n")]
    assert "form.submit()" not in block


def test_artist_opens_table_is_in_the_schema():
    import models
    assert any("CREATE TABLE IF NOT EXISTS artist_opens" in s for s in models.SCHEMA)

"""Redesign phase 3: artist page. Top tracks sorted by plays, malformed play
counts tolerated, photo hero, similar-artist photo tiles. Everything
external is stubbed; no network or database.
"""

import contextlib
import datetime
import json

import dashboard
import views_artist


def _render(monkeypatch, tracks, similar=(), photos=None, deezer_image=""):
    row = ("Massive Attack", "First paragraph.\n\nSecond paragraph.",
           datetime.datetime(2026, 8, 3), json.dumps(tracks))

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
            return {"artist": {"stats": {"listeners": "3985438", "playcount": "193600000"},
                               "tags": {"tag": [{"name": "trip-hop"}]}, "mbid": ""}}

    monkeypatch.setattr(views_artist, "db_cursor", fake_cm)
    monkeypatch.setattr(views_artist, "resolve_insight", lambda n, i: (i, False))
    monkeypatch.setattr(views_artist, "get_similar_artists", lambda n: list(similar))
    monkeypatch.setattr(views_artist, "get_artist_photos", lambda names: photos or {})
    monkeypatch.setattr(views_artist, "get_artist_media",
                        lambda n, m=None: {"spotify": {}, "deezer_image": deezer_image, "deezer_fans": 1_200_000})
    monkeypatch.setattr(views_artist, "get_artist_events", lambda n: [])
    monkeypatch.setattr(views_artist.http_requests, "get", lambda *a, **k: _Lastfm())
    return dashboard.app.test_client().get("/artist/Massive%20Attack").data.decode()


TRACKS = [
    {"name": "Teardrop", "playcount": "19006068"},
    {"name": "Angel", "playcount": "16558950"},
    {"name": "Inertia Creeps", "playcount": "6201834"},
    {"name": "Black Milk", "playcount": "5860965"},
    {"name": "Risingson", "playcount": "6338999"},   # stored out of order (real case)
]


def _track_names(html):
    section = html[html.index('id="tracks"'):html.index('id="insight"')]
    names = []
    for chunk in section.split('class="tx">')[1:]:
        names.append(chunk.split("<", 1)[0])
    return names


def test_top_tracks_are_sorted_by_plays(monkeypatch):
    html = _render(monkeypatch, TRACKS)
    assert _track_names(html) == ["Teardrop", "Angel", "Risingson", "Inertia Creeps", "Black Milk"]


def test_top_track_stat_uses_the_most_played_track(monkeypatch):
    shuffled = [TRACKS[2], TRACKS[0], TRACKS[1]]
    html = _render(monkeypatch, shuffled)
    stats = html[html.index('class="ar-stats"'):html.index('id="tracks"')]
    assert "Teardrop · 19,006,068" in stats
    assert "19M" in stats


def test_bars_are_scaled_to_the_top_track(monkeypatch):
    html = _render(monkeypatch, TRACKS)
    section = html[html.index('id="tracks"'):html.index('id="insight"')]
    assert "width:100.0%" in section
    assert "width:87.1%" in section    # Angel: 16,558,950 / 19,006,068


def test_malformed_playcount_counts_as_zero_instead_of_crashing(monkeypatch):
    html = _render(monkeypatch, [{"name": "Teardrop", "playcount": "19006068"},
                                 {"name": "Oddity", "playcount": "n/a"}])
    assert _track_names(html) == ["Teardrop", "Oddity"]
    assert "Something went wrong" not in html


def test_hero_uses_full_size_deezer_photo(monkeypatch):
    img = "https://cdn-images.dzcdn.net/images/artist/abc/250x250-000000-80-0-0.jpg"
    html = _render(monkeypatch, TRACKS, deezer_image=img)
    hero = html[html.index('class="ar-top"'):html.index('class="ar-name"')]
    assert "/abc/1000x1000-" in hero
    assert 'loading="lazy"' not in hero


def test_hero_without_photo_shows_initial(monkeypatch):
    html = _render(monkeypatch, TRACKS)
    hero = html[html.index('class="ar-top"'):html.index('class="ar-name"')]
    assert 'class="ph"' in hero and "<img" not in hero


def test_similar_artists_render_as_photo_tiles(monkeypatch):
    photos = {"Portishead": {"image": "https://cdn-images.dzcdn.net/images/artist/p/250x250-000000-80-0-0.jpg"}}
    html = _render(monkeypatch, TRACKS, similar=["Portishead", "Hird"], photos=photos)
    similar = html[html.index('id="similar"'):html.index('id="compare"')]
    assert "/p/500x500-" in similar
    assert 'href="/artist/Portishead"' in similar and 'href="/artist/Hird"' in similar
    assert similar.count("<img") == 1   # Hird has no photo: name only, no broken image


def test_big_number_stats_are_compact(monkeypatch):
    html = _render(monkeypatch, TRACKS)
    stats = html[html.index('class="ar-stats"'):html.index('id="tracks"')]
    assert "4M" in stats          # 3,985,438 listeners
    assert "Last.fm · 3,985,438" in stats
    assert "193.6M" in stats
    assert "1.2M" in stats

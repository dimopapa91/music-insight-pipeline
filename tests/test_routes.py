"""Route smoke tests using Flask's test client with the DB mocked."""

import contextlib
import datetime

import dashboard
import views_main


class FakeCursor:
    def __init__(self, one=None, all_rows=None):
        self._one = one
        self._all = all_rows or []

    def execute(self, *args, **kwargs):
        pass

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


def _fake_db_cursor(one=None, all_rows=None):
    @contextlib.contextmanager
    def cm(commit=False):
        yield FakeCursor(one=one, all_rows=all_rows)
    return cm


def test_api_stats_returns_json_and_cors(monkeypatch):
    monkeypatch.setattr(
        views_main, "db_cursor",
        _fake_db_cursor(one=(5, 3, datetime.datetime(2026, 7, 5))),
    )
    client = dashboard.app.test_client()
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total_searches"] == 5
    assert data["unique_artists"] == 3
    assert resp.headers["Access-Control-Allow-Origin"] == "https://dimospapageorgiou.com"


def test_api_artists_returns_list(monkeypatch):
    monkeypatch.setattr(
        views_main, "db_cursor",
        _fake_db_cursor(all_rows=[("Radiohead",), ("SZA",)]),
    )
    client = dashboard.app.test_client()
    resp = client.get("/api/artists")
    assert resp.status_code == 200
    assert resp.get_json() == ["Radiohead", "SZA"]


def test_api_stats_degrades_gracefully_on_db_error(monkeypatch):
    @contextlib.contextmanager
    def boom(commit=False):
        raise RuntimeError("db down")
        yield  # pragma: no cover
    monkeypatch.setattr(views_main, "db_cursor", boom)
    client = dashboard.app.test_client()
    resp = client.get("/api/stats")
    # Should not 500 — returns empty JSON with CORS header still set
    assert resp.status_code == 200
    assert resp.get_json() == {}

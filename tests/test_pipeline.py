"""Tests for the ETL pipeline's Last.fm fetch, with the network mocked."""

import pytest

import pipeline


class FakeResponse:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise pipeline.requests.exceptions.HTTPError("bad status")


def test_get_top_tracks_success(monkeypatch):
    payload = {"toptracks": {"track": [{"name": "Track One", "playcount": "100"}]}}
    monkeypatch.setattr(pipeline.requests, "get", lambda *a, **k: FakeResponse(payload))
    tracks = pipeline.get_top_tracks("Some Artist")
    assert tracks[0]["name"] == "Track One"


def test_get_top_tracks_api_error_raises(monkeypatch):
    payload = {"error": 6, "message": "The artist you supplied could not be found"}
    monkeypatch.setattr(pipeline.requests, "get", lambda *a, **k: FakeResponse(payload))
    with pytest.raises(ValueError):
        pipeline.get_top_tracks("Nonexistent Artist")


def test_get_top_tracks_missing_data_raises(monkeypatch):
    monkeypatch.setattr(pipeline.requests, "get", lambda *a, **k: FakeResponse({}))
    with pytest.raises(ValueError):
        pipeline.get_top_tracks("Empty")

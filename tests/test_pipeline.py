"""Tests for the ETL pipeline: Last.fm fetch (network mocked), and the
provider-resilience hotfix — Claude analysis is optional enrichment that
must never abort a search, while Last.fm and the database save remain core
and still propagate their own failures exactly as before.
"""

import logging
from types import SimpleNamespace

import pytest

import pipeline

TRACKS = [{"name": "Track One", "playcount": "100"}]


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


# ── analyse_with_claude: raises on failure, callers decide fatality ────

class _FakeMessages:
    def __init__(self, text=None, exception=None):
        self._text = text
        self._exception = exception

    def create(self, **kwargs):
        if self._exception:
            raise self._exception
        return SimpleNamespace(content=[SimpleNamespace(text=self._text)])


class _FakeClient:
    def __init__(self, text=None, exception=None):
        self.messages = _FakeMessages(text, exception)


def test_analyse_with_claude_success(monkeypatch):
    monkeypatch.setattr(pipeline, "_get_anthropic_client", lambda: _FakeClient(text="A great analysis."))
    result = pipeline.analyse_with_claude("Some Artist", TRACKS)
    assert result == "A great analysis."


def test_analyse_with_claude_raises_on_failure(monkeypatch):
    monkeypatch.setattr(pipeline, "_get_anthropic_client", lambda: _FakeClient(exception=RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        pipeline.analyse_with_claude("Some Artist", TRACKS)


# ── Anthropic client must be lazy, not built at module import time ────
#
# pipeline.py used to do `client = anthropic.Anthropic(api_key=...)` at
# module scope, so a missing/invalid ANTHROPIC_API_KEY risked failing
# `import pipeline` itself — before run_pipeline() ever got a chance to
# treat Claude as optional. _get_anthropic_client() defers construction to
# call time, inside analyse_with_claude(), so that failure mode is gone.

def test_pipeline_module_has_no_eager_anthropic_client_attribute():
    assert not hasattr(pipeline, "client")


def test_no_module_level_anthropic_construction():
    import inspect
    src = inspect.getsource(pipeline)
    module_level = src[:src.index("\ndef ")]  # everything before the first def
    assert "anthropic.Anthropic(" not in module_level


def test_get_anthropic_client_reads_the_key_lazily_at_call_time(monkeypatch):
    seen = {}

    def fake_anthropic_cls(api_key=None):
        seen["api_key"] = api_key
        return _FakeClient(text="ok")

    monkeypatch.setattr(pipeline.anthropic, "Anthropic", fake_anthropic_cls)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-lazy-value-12345")
    pipeline._get_anthropic_client()
    assert seen["api_key"] == "sk-test-lazy-value-12345"


def test_missing_api_key_fails_in_a_controlled_way_inside_analyse_with_claude(monkeypatch):
    # Removed after import — proves the failure happens at CALL time, not
    # at `import pipeline` time (load_dotenv() already ran at import).
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        pipeline.analyse_with_claude("Some Artist", TRACKS)


def test_run_pipeline_with_missing_api_key_still_saves_core_data(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(pipeline, "get_top_tracks", lambda artist_name: TRACKS)

    calls = {"claude_attempts": 0, "db": []}
    real_analyse = pipeline.analyse_with_claude

    def counting_analyse(artist_name, tracks):
        calls["claude_attempts"] += 1
        return real_analyse(artist_name, tracks)  # exercises the REAL missing-key path

    monkeypatch.setattr(pipeline, "analyse_with_claude", counting_analyse)
    monkeypatch.setattr(pipeline, "save_to_db",
                         lambda artist_name, tracks, insight, user_id=None: calls["db"].append((artist_name, tracks, insight, user_id)))

    result = pipeline.run_pipeline("Some Artist", user_id=3)

    assert calls["claude_attempts"] == 1  # attempted once, no retry
    assert len(calls["db"]) == 1
    artist_name, tracks, insight, user_id = calls["db"][0]
    assert insight == ""  # schema-compatible unavailable representation
    assert user_id == 3
    assert result == ""  # falsey, no exception raised out of run_pipeline


def test_missing_api_key_message_never_reaches_the_logs(monkeypatch, caplog):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with caplog.at_level(logging.ERROR):
        try:
            pipeline.analyse_with_claude("Some Artist", TRACKS)
        except RuntimeError as e:
            pipeline._log_claude_failure("Some Artist", e)
    assert "Anthropic API key is not configured" not in caplog.text
    assert "RuntimeError" in caplog.text


# ── save_to_db: core, must still raise on failure ──────────────────────

def _fake_conn(calls, fail=False):
    import psycopg2

    class FakeCur:
        def execute(self, sql, params=None):
            if fail:
                raise psycopg2.Error("db down")
            calls.append((sql, params))

        def close(self):
            pass

    class FakeConn:
        def cursor(self):
            return FakeCur()

        def commit(self):
            calls.append(("COMMIT",))

        def rollback(self):
            calls.append(("ROLLBACK",))

        def close(self):
            calls.append(("CLOSE",))

    return FakeConn()


def test_save_to_db_success(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, "get_db_connection", lambda: _fake_conn(calls))
    pipeline.save_to_db("Some Artist", TRACKS, "an insight", user_id=7)
    inserts = [c for c in calls if c[0] not in ("COMMIT", "CLOSE")]
    assert len(inserts) == 1
    sql, params = inserts[0]
    assert "INSERT INTO searches" in sql
    assert params == ("Some Artist", '[{"name": "Track One", "playcount": "100"}]', "an insight", 7)
    assert ("COMMIT",) in calls
    assert ("CLOSE",) in calls


def test_save_to_db_failure_propagates_and_rolls_back(monkeypatch):
    import psycopg2
    calls = []
    monkeypatch.setattr(pipeline, "get_db_connection", lambda: _fake_conn(calls, fail=True))
    with pytest.raises(psycopg2.Error):
        pipeline.save_to_db("Some Artist", TRACKS, "an insight")
    assert ("ROLLBACK",) in calls
    assert ("CLOSE",) in calls


# ── run_pipeline: Claude success — full happy path ─────────────────────

def test_run_pipeline_claude_success_returns_and_saves_insight(monkeypatch):
    calls = {"lastfm": 0, "claude": 0, "db": []}
    monkeypatch.setattr(pipeline, "get_top_tracks", lambda artist_name: calls.update(lastfm=calls["lastfm"] + 1) or TRACKS)

    def fake_analyse(artist_name, tracks):
        calls["claude"] += 1
        return "Generated insight"

    monkeypatch.setattr(pipeline, "analyse_with_claude", fake_analyse)
    monkeypatch.setattr(pipeline, "save_to_db", lambda artist_name, tracks, insight, user_id=None: calls["db"].append((artist_name, tracks, insight, user_id)))

    result = pipeline.run_pipeline("Some Artist", user_id=42)

    assert calls["lastfm"] == 1
    assert calls["claude"] == 1
    assert calls["db"] == [("Some Artist", TRACKS, "Generated insight", 42)]
    assert result == "Generated insight"


# ── run_pipeline: Claude failure is non-fatal (the actual hotfix) ──────

def test_run_pipeline_claude_failure_still_saves_core_data_and_does_not_raise(monkeypatch):
    calls = {"claude_attempts": 0, "db": []}
    monkeypatch.setattr(pipeline, "get_top_tracks", lambda artist_name: TRACKS)

    def fake_analyse(artist_name, tracks):
        calls["claude_attempts"] += 1
        raise RuntimeError("insufficient credits")

    monkeypatch.setattr(pipeline, "analyse_with_claude", fake_analyse)
    monkeypatch.setattr(pipeline, "save_to_db", lambda artist_name, tracks, insight, user_id=None: calls["db"].append((artist_name, tracks, insight, user_id)))

    result = pipeline.run_pipeline("Some Artist", user_id=7)

    assert calls["claude_attempts"] == 1  # attempted once, no retry loop
    assert len(calls["db"]) == 1
    artist_name, tracks, insight, user_id = calls["db"][0]
    assert artist_name == "Some Artist"
    assert tracks == TRACKS
    assert insight == ""  # schema-compatible ("" — claude_insight is NOT NULL), never None
    assert user_id == 7
    assert not result  # falsey insight returned to the caller; no exception raised


def test_run_pipeline_claude_failure_with_a_real_anthropic_error_type(monkeypatch):
    import anthropic
    import httpx
    resp = httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    exc = anthropic.APIStatusError("insufficient_quota", response=resp, body={"error": {"message": "no credits"}})

    monkeypatch.setattr(pipeline, "get_top_tracks", lambda artist_name: TRACKS)
    monkeypatch.setattr(pipeline, "_get_anthropic_client", lambda: _FakeClient(exception=exc))
    saved = {}
    monkeypatch.setattr(pipeline, "save_to_db", lambda artist_name, tracks, insight, user_id=None: saved.update(insight=insight))

    result = pipeline.run_pipeline("Some Artist")
    assert result == ""
    assert saved["insight"] == ""


# ── run_pipeline: Last.fm (core) failure propagates; nothing else runs ──

def test_run_pipeline_lastfm_failure_propagates_and_skips_claude_and_db(monkeypatch):
    calls = {"claude": 0, "db": 0}

    def fake_get_top_tracks(artist_name):
        raise ValueError("No track data found for artist: Nonexistent")

    monkeypatch.setattr(pipeline, "get_top_tracks", fake_get_top_tracks)
    monkeypatch.setattr(pipeline, "analyse_with_claude", lambda *a, **k: calls.update(claude=calls["claude"] + 1))
    monkeypatch.setattr(pipeline, "save_to_db", lambda *a, **k: calls.update(db=calls["db"] + 1))

    with pytest.raises(ValueError):
        pipeline.run_pipeline("Nonexistent")

    assert calls["claude"] == 0
    assert calls["db"] == 0


# ── run_pipeline: DB (core) failure propagates ─────────────────────────

def test_run_pipeline_db_failure_propagates_and_search_not_reported_successful(monkeypatch):
    monkeypatch.setattr(pipeline, "get_top_tracks", lambda artist_name: TRACKS)
    monkeypatch.setattr(pipeline, "analyse_with_claude", lambda artist_name, tracks: "an insight")
    monkeypatch.setattr(pipeline, "save_to_db", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))

    with pytest.raises(RuntimeError):
        pipeline.run_pipeline("Some Artist")


# ── safe logging: no secrets, no payload/message-body leakage ─────────

def test_log_claude_failure_never_logs_the_exception_message_or_body(caplog):
    import anthropic
    import httpx
    resp = httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
                           headers={"request-id": "req_abc123"})
    exc = anthropic.APIStatusError("SECRET_PROVIDER_FAILURE_12345", response=resp,
                                    body={"error": {"message": "SECRET_PROVIDER_FAILURE_12345"}})
    with caplog.at_level(logging.ERROR):
        pipeline._log_claude_failure("Some Artist", exc)
    assert "SECRET_PROVIDER_FAILURE_12345" not in caplog.text
    assert "APIStatusError" in caplog.text
    assert "400" in caplog.text
    assert "req_abc123" in caplog.text


def test_log_claude_failure_never_logs_the_api_key(caplog):
    import os
    key = os.getenv("ANTHROPIC_API_KEY", "")
    with caplog.at_level(logging.ERROR):
        pipeline._log_claude_failure("Some Artist", RuntimeError("boom"))
    if key:
        assert key not in caplog.text


def test_log_claude_failure_handles_a_plain_exception_without_status_or_response(caplog):
    with caplog.at_level(logging.ERROR):
        pipeline._log_claude_failure("Some Artist", TimeoutError("timed out"))
    assert "TimeoutError" in caplog.text
    assert "status_code=None" in caplog.text

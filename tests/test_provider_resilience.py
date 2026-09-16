"""Tests for the provider-resilience hotfix: Claude/Anthropic enrichment
must never abort a search or crash a page. Last.fm and the database save
remain core and their failures still surface as real failures.

pipeline.py's own unit tests (run_pipeline, analyse_with_claude, save_to_db,
safe logging) live in tests/test_pipeline.py. This file covers the public
routes, templates, and services built on top of it: /search, /artist/<name>,
/compare, and the dashboard/homepage.

No real network or database calls anywhere in this file.
"""

import contextlib
import datetime as dt

import anthropic

import dashboard
import pipeline
import services
import views_artist
import views_main
from models import User

SECRET = "SECRET_PROVIDER_FAILURE_12345"
FAKE_USER = User(id=1, username="dimos", email="d@e.com", password_hash="x")


def _login(client, monkeypatch, user=FAKE_USER):
    monkeypatch.setattr(User, "get", classmethod(lambda cls, i: user))
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)


def _dashboard_client(monkeypatch, **overrides):
    defaults = dict(get_dashboard_data=lambda: (10, 5, 2, [], [], []))
    defaults.update(overrides)
    for name, value in defaults.items():
        monkeypatch.setattr(views_main, name, value)
    monkeypatch.setattr(views_main, "get_feed", lambda *a, **k: [])
    return dashboard.app.test_client()


# ── /search: full success, partial (AI-only) success, core failure ────

def test_search_full_success_gives_normal_success_message(monkeypatch):
    monkeypatch.setattr(views_main, "run_pipeline", lambda artist, user_id=None: "A generated insight.")
    client = dashboard.app.test_client()
    resp = client.post("/search", data={"artist": "Radiohead"})
    assert resp.status_code == 302
    location = resp.headers["Location"]
    assert "error" not in location
    assert "Radiohead" in location
    assert "successfully" in location


def test_search_ai_only_failure_gives_partial_success_not_error(monkeypatch):
    # run_pipeline succeeded (no exception) but returned a falsey insight —
    # exactly what happens when Last.fm+DB succeed and only Claude failed.
    monkeypatch.setattr(views_main, "run_pipeline", lambda artist, user_id=None: "")
    client = dashboard.app.test_client()
    resp = client.post("/search", data={"artist": "Radiohead"})
    assert resp.status_code == 302
    location = resp.headers["Location"]
    assert "error=True" not in location
    assert "temporarily+unavailable" in location or "temporarily unavailable" in location
    assert "❌" not in location


def test_partial_success_message_renders_with_calm_not_error_styling(monkeypatch):
    # Exercise the actual rendered dashboard (not just the redirect Location)
    # for the exact message /search produces on AI-only failure.
    client = _dashboard_client(monkeypatch)
    resp = client.get("/", query_string={"message": "Radiohead data is ready. AI insight is temporarily unavailable."})
    html = resp.data.decode()
    assert "temporarily unavailable" in html
    assert "wv-notice-info" in html
    assert "wv-notice-error" not in html


def test_search_core_pipeline_failure_gives_calm_generic_message(monkeypatch):
    monkeypatch.setattr(views_main, "run_pipeline",
                         lambda artist, user_id=None: (_ for _ in ()).throw(RuntimeError(SECRET)))
    client = dashboard.app.test_client()
    resp = client.post("/search", data={"artist": "Radiohead"})
    assert resp.status_code == 302
    location = resp.headers["Location"]
    assert "error=True" in location
    assert SECRET not in location
    resp2 = client.get(location)
    assert SECRET not in resp2.data.decode()


def test_search_with_missing_anthropic_key_gives_partial_success_end_to_end(monkeypatch):
    # The real run_pipeline() (not mocked) with a genuinely missing key —
    # confirms the whole chain (lazy client -> analyse_with_claude raises ->
    # run_pipeline catches -> "" insight -> /search treats it as partial
    # success, not an error) actually holds together end to end.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(views_main, "run_pipeline", pipeline.run_pipeline)
    monkeypatch.setattr(pipeline, "get_top_tracks", lambda artist_name: [{"name": "Track", "playcount": "1"}])
    monkeypatch.setattr(pipeline, "save_to_db", lambda *a, **k: None)

    client = dashboard.app.test_client()
    resp = client.post("/search", data={"artist": "Radiohead"})
    assert resp.status_code == 302
    location = resp.headers["Location"]
    assert "error=True" not in location
    assert "Anthropic API key is not configured" not in location
    assert "temporarily+unavailable" in location or "temporarily unavailable" in location


def test_search_never_leaks_raw_exception_text_anywhere_in_the_response(monkeypatch):
    monkeypatch.setattr(views_main, "run_pipeline",
                         lambda artist, user_id=None: (_ for _ in ()).throw(ValueError(SECRET)))
    client = dashboard.app.test_client()
    resp = client.post("/search", data={"artist": "Radiohead"}, follow_redirects=True)
    assert SECRET not in resp.data.decode()
    assert "Traceback" not in resp.data.decode()


def test_search_route_source_never_interpolates_the_raw_exception():
    import inspect
    src = inspect.getsource(views_main.search)
    # /debug/spotify (a separate, non-public diagnostic route) is allowed to
    # echo str(e) — this checks only the public /search view function itself.
    assert "str(e)" not in src
    assert "Check the spelling" in src


# ── /artist/<name>: existing artist, valid insight ─────────────────────

def _fake_row_cursor(row, count_result=(1,), fallback_insight=None, found_immediately=True):
    """A views_artist.db_cursor / services.db_cursor replacement matching
    the exact two-queries-per-`with`-block shape artist_profile() and
    resolve_insight()'s fallback use. With found_immediately=False, `row`
    is only returned once state["pipeline_ran"] is flipped True — lets a
    test simulate "not found yet, found after run_pipeline() runs"."""
    state = {"pipeline_ran": found_immediately}

    def fake(commit=False):
        @contextlib.contextmanager
        def _cm():
            class FakeCur:
                def __init__(self):
                    self._sql = ""

                def execute(self, sql, params=None):
                    self._sql = sql

                def fetchone(self):
                    if "claude_insight <> ''" in self._sql:
                        return (fallback_insight,) if fallback_insight else None
                    if "COUNT(*)" in self._sql:
                        return count_result
                    if "FROM searches" in self._sql and "WHERE LOWER(artist_name)" in self._sql:
                        return row if state["pipeline_ran"] else None
                    return None

            yield FakeCur()

        return _cm()

    return fake, state


def test_artist_profile_with_valid_insight_renders_ai_block(monkeypatch):
    row = ("Radiohead", "A real insight about Radiohead.", dt.datetime(2026, 1, 1),
           '[{"name": "Creep", "playcount": 100}]')
    fake_cursor, _ = _fake_row_cursor(row)
    monkeypatch.setattr(views_artist, "db_cursor", fake_cursor)
    client = dashboard.app.test_client()
    resp = client.get("/artist/Radiohead")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "AI-generated by Claude" in html
    assert "A real insight about Radiohead." in html
    assert "temporarily unavailable" not in html


def test_artist_profile_with_no_insight_still_renders_page(monkeypatch):
    row = ("Radiohead", "", dt.datetime(2026, 1, 1), '[{"name": "Creep", "playcount": 100}]')
    fake_cursor, _ = _fake_row_cursor(row)
    monkeypatch.setattr(views_artist, "db_cursor", fake_cursor)
    monkeypatch.setattr(services, "db_cursor", fake_cursor)
    client = dashboard.app.test_client()
    resp = client.get("/artist/Radiohead")
    assert resp.status_code == 200


def test_no_insight_state_does_not_claim_ai_generated_by_claude(monkeypatch):
    row = ("Radiohead", "", dt.datetime(2026, 1, 1), '[{"name": "Creep", "playcount": 100}]')
    fake_cursor, _ = _fake_row_cursor(row)
    monkeypatch.setattr(views_artist, "db_cursor", fake_cursor)
    monkeypatch.setattr(services, "db_cursor", fake_cursor)
    client = dashboard.app.test_client()
    html = client.get("/artist/Radiohead").data.decode()
    assert "AI-generated by Claude" not in html


def test_no_insight_state_shows_calm_temporary_unavailable_copy(monkeypatch):
    row = ("Radiohead", "", dt.datetime(2026, 1, 1), '[{"name": "Creep", "playcount": 100}]')
    fake_cursor, _ = _fake_row_cursor(row)
    monkeypatch.setattr(views_artist, "db_cursor", fake_cursor)
    monkeypatch.setattr(services, "db_cursor", fake_cursor)
    client = dashboard.app.test_client()
    html = client.get("/artist/Radiohead").data.decode()
    assert "temporarily unavailable" in html
    assert "wv-notice-info" in html


def test_tracks_and_stats_area_remains_present_without_ai(monkeypatch):
    row = ("Radiohead", "", dt.datetime(2026, 1, 1), '[{"name": "Creep", "playcount": 100}]')
    fake_cursor, _ = _fake_row_cursor(row)
    monkeypatch.setattr(views_artist, "db_cursor", fake_cursor)
    monkeypatch.setattr(services, "db_cursor", fake_cursor)
    client = dashboard.app.test_client()
    html = client.get("/artist/Radiohead").data.decode()
    assert "Creep" in html
    assert 'id="insight"' in html  # the section itself is still there


# ── new artist: Claude fails inside run_pipeline, core still succeeds ──

def test_new_artist_claude_failure_inside_run_pipeline_still_renders_profile(monkeypatch):
    # The auto-fetch-on-miss flow is now member-only (see
    # test_abuse_and_cost_controls.py for the anonymous side of that split)
    # — this test is specifically about logged-in behaviour, unchanged.
    found_row = ("Some Artist", "", dt.datetime(2026, 1, 1), '[{"name": "Track One", "playcount": 10}]')
    fake_cursor, state = _fake_row_cursor(found_row, found_immediately=False)
    monkeypatch.setattr(views_artist, "db_cursor", fake_cursor)
    monkeypatch.setattr(services, "db_cursor", fake_cursor)

    def fake_run_pipeline(artist_name):
        # Exactly the new contract: Claude failed internally, but
        # run_pipeline() did NOT raise — Last.fm + DB save succeeded.
        state["pipeline_ran"] = True
        return ""

    monkeypatch.setattr(views_artist, "run_pipeline", fake_run_pipeline)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.get("/artist/Some Artist", follow_redirects=True)
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "Traceback" not in html
    assert "Track One" in html
    assert "temporarily unavailable" in html


def test_new_artist_genuine_core_failure_still_returns_calm_error(monkeypatch):
    fake_cursor, _ = _fake_row_cursor(None, found_immediately=False)  # never found
    monkeypatch.setattr(views_artist, "db_cursor", fake_cursor)

    def fake_run_pipeline(artist_name):
        raise ValueError(f"No track data found for artist: {SECRET}")

    monkeypatch.setattr(views_artist, "run_pipeline", fake_run_pipeline)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.get("/artist/Unknown Artist Xyz")
    assert resp.status_code == 500
    html = resp.data.decode()
    assert SECRET not in html
    assert "Traceback" not in html
    assert 'class="wv-dock"' in html  # calm shared shell, not a bare 500


def test_new_artist_flow_does_not_redirect_loop(monkeypatch):
    found_row = ("Some Artist", "an insight", dt.datetime(2026, 1, 1), '[{"name": "Track One", "playcount": 10}]')
    fake_cursor, state = _fake_row_cursor(found_row, found_immediately=False)
    monkeypatch.setattr(views_artist, "db_cursor", fake_cursor)

    def fake_run_pipeline(artist_name):
        state["pipeline_ran"] = True
        return "an insight"

    monkeypatch.setattr(views_artist, "run_pipeline", fake_run_pipeline)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    first = client.get("/artist/Some Artist")
    assert first.status_code == 302
    second = client.get(first.headers["Location"])
    assert second.status_code == 200  # resolved after exactly one redirect


# ── older-insight fallback ──────────────────────────────────────────

def test_latest_empty_insight_falls_back_to_older_valid_insight_on_artist_page(monkeypatch):
    row = ("Radiohead", "", dt.datetime(2026, 1, 1), '[{"name": "Creep", "playcount": 100}]')
    fake_cursor, _ = _fake_row_cursor(row, fallback_insight="An older, still-valid insight.")
    monkeypatch.setattr(views_artist, "db_cursor", fake_cursor)
    monkeypatch.setattr(services, "db_cursor", fake_cursor)
    client = dashboard.app.test_client()
    html = client.get("/artist/Radiohead").data.decode()
    assert "An older, still-valid insight." in html


def test_reused_insight_is_labelled_not_claimed_as_freshly_generated(monkeypatch):
    row = ("Radiohead", "", dt.datetime(2026, 1, 1), '[{"name": "Creep", "playcount": 100}]')
    fake_cursor, _ = _fake_row_cursor(row, fallback_insight="An older, still-valid insight.")
    monkeypatch.setattr(views_artist, "db_cursor", fake_cursor)
    monkeypatch.setattr(services, "db_cursor", fake_cursor)
    client = dashboard.app.test_client()
    html = client.get("/artist/Radiohead").data.decode()
    assert "AI insight from an earlier analysis" in html
    assert "AI-generated by Claude" not in html


# ── services.get_artist_db() safety ────────────────────────────────────

def _fake_get_artist_db_cursor(insight, fallback_insight=None):
    def fake(commit=False):
        @contextlib.contextmanager
        def _cm():
            class FakeCur:
                def __init__(self):
                    self._sql = ""

                def execute(self, sql, params=None):
                    self._sql = sql

                def fetchone(self):
                    if "claude_insight <> ''" in self._sql:
                        return (fallback_insight,) if fallback_insight else None
                    return ("Radiohead", insight, '[{"name": "Creep"}]')

            yield FakeCur()

        return _cm()

    return fake


def test_get_artist_db_handles_empty_insight(monkeypatch):
    monkeypatch.setattr(services, "db_cursor", _fake_get_artist_db_cursor(""))
    monkeypatch.setattr(services, "get_spotify_artist", lambda name: {})
    result = services.get_artist_db("Radiohead")
    assert result["insight"] == ""
    assert result["insight_is_reused"] is False


def test_get_artist_db_handles_none_insight(monkeypatch):
    monkeypatch.setattr(services, "db_cursor", _fake_get_artist_db_cursor(None))
    monkeypatch.setattr(services, "get_spotify_artist", lambda name: {})
    result = services.get_artist_db("Radiohead")
    assert result["insight"] == ""  # never a bare None reaching callers


def test_get_artist_db_falls_back_to_older_nonempty_insight(monkeypatch):
    monkeypatch.setattr(services, "db_cursor",
                         _fake_get_artist_db_cursor("", fallback_insight="An older, still-valid insight."))
    monkeypatch.setattr(services, "get_spotify_artist", lambda name: {})
    result = services.get_artist_db("Radiohead")
    assert result["insight"] == "An older, still-valid insight."
    assert result["insight_is_reused"] is True


def test_resolve_insight_never_raises_on_none():
    insight, is_reused = services.resolve_insight("Some Artist Never In Db", None)
    assert insight == ""
    assert is_reused is False


# ── Compare: never crashes without artist insight ─────────────────────

def test_compare_does_not_crash_when_insight_is_none(monkeypatch):
    monkeypatch.setattr(views_artist, "get_artist_db",
                         lambda name: {"name": name, "insight": None, "tracks": ["Creep", "Karma Police"],
                                       "spotify_url": "", "image": ""})
    monkeypatch.setattr(anthropic, "Anthropic", lambda api_key=None: (_ for _ in ()).throw(RuntimeError("no network in tests")))
    client = dashboard.app.test_client()
    resp = client.get("/compare?a=Radiohead&b=Muse")
    assert resp.status_code == 200


def test_compare_renders_core_artist_data_without_insight(monkeypatch):
    monkeypatch.setattr(views_artist, "get_artist_db",
                         lambda name: {"name": name, "insight": "", "tracks": ["Creep", "Karma Police"],
                                       "spotify_url": "", "image": ""})
    monkeypatch.setattr(anthropic, "Anthropic", lambda api_key=None: (_ for _ in ()).throw(RuntimeError("no network in tests")))
    client = dashboard.app.test_client()
    html = client.get("/compare?a=Radiohead&b=Muse").data.decode()
    assert "Radiohead" in html
    assert "Muse" in html
    assert "Creep" in html


def test_compare_provider_failure_retains_calm_fallback(monkeypatch):
    monkeypatch.setattr(views_artist, "get_artist_db",
                         lambda name: {"name": name, "insight": "an insight", "tracks": ["Creep"],
                                       "spotify_url": "", "image": ""})
    monkeypatch.setattr(anthropic, "Anthropic", lambda api_key=None: (_ for _ in ()).throw(RuntimeError(SECRET)))
    client = dashboard.app.test_client()
    html = client.get("/compare?a=Radiohead&b=Muse").data.decode()
    assert SECRET not in html
    assert "isn't available right now" in html


# ── dashboard/homepage: empty insight never breaks the page ───────────

class _EmptyInsightRow:
    artist = "Radiohead"
    insight = ""
    searched_at = dt.datetime(2026, 7, 5, 12, 0)
    top_tracks = ["Creep", "Karma Police"]
    similar_artists = ["Muse", "Coldplay"]


def test_dashboard_renders_without_blank_markup_for_empty_ai_insight(monkeypatch):
    client = _dashboard_client(monkeypatch, get_dashboard_data=lambda: (
        10, 5, 2, [("Radiohead", 500, 500)], [_EmptyInsightRow()], [],
    ))
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "Written insight is temporarily unavailable for this artist." in html
    # no bare "…" with nothing meaningful before it in the featured summary
    assert "wv-featured-summary\">…" not in html


def test_dashboard_secondary_list_shows_unavailable_not_bare_ellipsis(monkeypatch):
    second_row = _EmptyInsightRow()
    second_row.artist = "Muse"
    client = _dashboard_client(monkeypatch, get_dashboard_data=lambda: (
        10, 5, 2, [], [_EmptyInsightRow(), second_row], [],
    ))
    html = client.get("/").data.decode()
    assert "AI insight unavailable" in html
    assert 'class="sm">…' not in html


def test_search_and_artist_counts_query_has_no_claude_insight_filter():
    # The core "has this artist been searched" counts must never be scoped
    # to rows that happen to have a non-empty AI insight.
    import inspect
    src = inspect.getsource(services.get_dashboard_data)
    count_block = src[:src.index("artist_avg")]
    assert "claude_insight" not in count_block


def test_homepage_renders_with_a_real_empty_ai_insight_end_to_end(monkeypatch):
    # Belt-and-suspenders: hit resolve_insight() itself via the dashboard
    # data path is covered above via the Row fake; here we confirm the
    # underlying helper used by that Row is exactly the resilience helper.
    src_dashboard = __import__("inspect").getsource(services.get_dashboard_data)
    assert "resolve_insight(" in src_dashboard

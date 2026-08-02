"""Tests for the self-hosted analytics feature: schema, the after_request
recorder (and what it correctly skips), the daily-rotating visitor hash,
/admin/stats access control, and graceful GeoIP fallback.
"""

import contextlib
import datetime

from flask import Response

import analytics
import dashboard
import views_main
from models import User

FAKE_ADMIN = User(id=1, username="dimos", email="d@e.com", password_hash="x")
FAKE_MEMBER = User(id=2, username="alice", email="a@e.com", password_hash="x")


def _login(client, monkeypatch, user):
    monkeypatch.setattr(User, "get", classmethod(lambda cls, i: user))
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)


def _recording_db_cursor(monkeypatch, target=analytics):
    """Patch `target.db_cursor` with a fake that records every executed
    statement, so we can assert exactly what analytics tried to write."""
    calls = []

    class FakeCur:
        def execute(self, sql, params=None):
            calls.append((sql, params))

        def fetchone(self):
            return None

        def fetchall(self):
            return []

    @contextlib.contextmanager
    def cm(commit=False):
        yield FakeCur()

    monkeypatch.setattr(target, "db_cursor", cm)
    return calls


# ── 1: schema ──

def test_analytics_events_created_by_schema_init(monkeypatch):
    executed = []

    class FakeCur:
        def execute(self, sql, params=None):
            executed.append(sql)

    @contextlib.contextmanager
    def fake_cm(commit=False):
        yield FakeCur()

    import models
    monkeypatch.setattr(models, "db_cursor", fake_cm)
    models.init_db()

    joined = "\n".join(executed)
    assert "CREATE TABLE IF NOT EXISTS analytics_events" in joined
    assert "idx_analytics_created_at" in joined
    assert "idx_analytics_country" in joined
    assert "idx_analytics_path" in joined


# ── 2: after_request recording + skip rules ──

def test_after_request_inserts_one_row_for_normal_html_get(monkeypatch):
    calls = _recording_db_cursor(monkeypatch)
    client = dashboard.app.test_client()
    resp = client.get("/about")
    assert resp.status_code == 200
    inserts = [c for c in calls if c[0].strip().startswith("INSERT INTO analytics_events")]
    assert len(inserts) == 1


def test_after_request_skips_static_assets(monkeypatch):
    calls = _recording_db_cursor(monkeypatch)
    client = dashboard.app.test_client()
    client.get("/static/css/waveline.css")
    assert not any("analytics_events" in c[0] for c in calls)


def test_after_request_skips_api_routes(monkeypatch):
    calls = _recording_db_cursor(monkeypatch)

    class FakeCur:
        def execute(self, *a, **k):
            pass

        def fetchall(self):
            return []

    @contextlib.contextmanager
    def cm(commit=False):
        yield FakeCur()

    monkeypatch.setattr(views_main, "db_cursor", cm)
    client = dashboard.app.test_client()
    resp = client.get("/api/artists")
    assert resp.status_code == 200
    assert not any("analytics_events" in c[0] for c in calls)


def test_after_request_skips_non_get_requests(monkeypatch):
    calls = _recording_db_cursor(monkeypatch)
    client = dashboard.app.test_client()
    client.post("/news/refresh")
    assert not any("analytics_events" in c[0] for c in calls)


def test_should_record_skips_preview_admin_and_favicon():
    with dashboard.app.test_request_context("/preview", method="GET"):
        assert analytics._should_record(Response("{}", content_type="application/json")) is False
    with dashboard.app.test_request_context("/admin/stats", method="GET"):
        assert analytics._should_record(Response("<html></html>", content_type="text/html")) is False
    with dashboard.app.test_request_context("/favicon.ico", method="GET"):
        assert analytics._should_record(Response("", content_type="image/x-icon")) is False


def test_should_record_skips_non_html_content_type():
    with dashboard.app.test_request_context("/artist/Radiohead", method="GET"):
        assert analytics._should_record(Response("{}", content_type="application/json")) is False


def test_should_record_true_for_real_html_page():
    with dashboard.app.test_request_context("/about", method="GET"):
        assert analytics._should_record(Response("<html></html>", content_type="text/html; charset=utf-8")) is True


def test_record_pageview_never_raises_on_db_error(monkeypatch):
    @contextlib.contextmanager
    def boom(commit=False):
        raise RuntimeError("db down")
        yield  # pragma: no cover

    monkeypatch.setattr(analytics, "db_cursor", boom)
    client = dashboard.app.test_client()
    resp = client.get("/about")
    assert resp.status_code == 200  # analytics failure must not break the page


# ── 3: visitor_hash rotates daily, stable within a day ──

def test_visitor_hash_stable_within_a_day():
    day = datetime.datetime(2026, 7, 28, 9, 0, tzinfo=datetime.timezone.utc)
    later_same_day = datetime.datetime(2026, 7, 28, 23, 59, tzinfo=datetime.timezone.utc)
    h1 = analytics._visitor_hash("1.2.3.4", "UA/1", "secret", when=day)
    h2 = analytics._visitor_hash("1.2.3.4", "UA/1", "secret", when=later_same_day)
    assert h1 == h2


def test_visitor_hash_changes_on_a_different_date():
    day1 = datetime.datetime(2026, 7, 28, tzinfo=datetime.timezone.utc)
    day2 = datetime.datetime(2026, 7, 29, tzinfo=datetime.timezone.utc)
    h1 = analytics._visitor_hash("1.2.3.4", "UA/1", "secret", when=day1)
    h2 = analytics._visitor_hash("1.2.3.4", "UA/1", "secret", when=day2)
    assert h1 != h2


def test_visitor_hash_never_contains_the_raw_ip():
    day = datetime.datetime(2026, 7, 28, tzinfo=datetime.timezone.utc)
    h = analytics._visitor_hash("203.0.113.42", "UA/1", "secret", when=day)
    assert "203.0.113.42" not in h
    assert len(h) == 32


# ── 4: /admin/stats access control ──

def test_admin_stats_404_for_anonymous(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "dimos")
    client = dashboard.app.test_client()
    resp = client.get("/admin/stats")
    assert resp.status_code == 404


def test_admin_stats_404_for_logged_in_non_admin(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "dimos")
    client = dashboard.app.test_client()
    _login(client, monkeypatch, FAKE_MEMBER)
    resp = client.get("/admin/stats")
    assert resp.status_code == 404


def test_admin_stats_200_for_admin_user(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "dimos")
    monkeypatch.setattr(analytics, "get_stats", lambda: {
        "total_users": 1, "signups_per_day": [], "latest_signups": [],
        "total_pageviews": 0, "unique_visitors_today": 0, "unique_visitors_7d": 0,
        "unique_visitors_30d": 0, "top_countries": [], "top_paths": [], "top_referrers": [],
        "total_searches": 0, "searches_by_membership": {"members": 0, "anonymous": 0},
        "community": {"posts": 0, "follows": 0, "likes": 0, "comments": 0},
    })
    client = dashboard.app.test_client()
    _login(client, monkeypatch, FAKE_ADMIN)
    resp = client.get("/admin/stats")
    assert resp.status_code == 200
    assert b"Analytics" in resp.data


def test_admin_stats_404_when_admin_username_unset(monkeypatch):
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    client = dashboard.app.test_client()
    _login(client, monkeypatch, FAKE_ADMIN)
    resp = client.get("/admin/stats")
    assert resp.status_code == 404


# ── 5: GeoIP graceful fallback ──

def test_lookup_country_returns_none_when_geoip_db_path_unset(monkeypatch):
    monkeypatch.delenv("GEOIP_DB_PATH", raising=False)
    monkeypatch.setattr(analytics, "_geo_reader", None)
    monkeypatch.setattr(analytics, "_geo_initialised", False)
    assert analytics.lookup_country("8.8.8.8") is None


def test_init_geoip_handles_missing_file_gracefully(monkeypatch):
    monkeypatch.setenv("GEOIP_DB_PATH", "/no/such/file/GeoLite2-Country.mmdb")
    monkeypatch.setattr(analytics, "_geo_reader", None)
    monkeypatch.setattr(analytics, "_geo_initialised", False)
    analytics.init_geoip()  # must not raise
    assert analytics._geo_reader is None
    assert analytics.lookup_country("8.8.8.8") is None

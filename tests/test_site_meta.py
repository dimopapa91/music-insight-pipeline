"""Audit quick wins (27 Sep 2026): security headers, static caching, same-
origin check, OG/canonical tags, sitemap, favicon, agent discovery files,
refresh-analysis button. No network or real database."""

import contextlib
import datetime
import json

import dashboard
import site_meta
import views_main


def _client():
    return dashboard.app.test_client()


def _home(monkeypatch):
    monkeypatch.setattr(views_main, "get_dashboard_data", lambda: (0, 0, 0, [], [], []))
    monkeypatch.setattr(views_main, "get_feed", lambda *a, **k: [])
    monkeypatch.setattr(dashboard, "get_site_pulse", lambda: {"recent": []})
    return _client().get("/")


def test_security_headers_on_pages(monkeypatch):
    resp = _home(monkeypatch)
    for header in ("Strict-Transport-Security", "X-Content-Type-Options", "X-Frame-Options",
                   "Referrer-Policy", "Permissions-Policy", "Content-Security-Policy"):
        assert resp.headers.get(header), header
    csp = resp.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp and "https://fonts.gstatic.com" in csp


def test_static_assets_are_versioned_and_cached_long(monkeypatch):
    html = _home(monkeypatch).data.decode()
    assert "/static/css/waveline.css?v=" in html
    assert "/static/js/nav.js?v=" in html
    url = html.split('/static/css/waveline.css?v=')[1].split('"')[0]
    resp = _client().get("/static/css/waveline.css?v=" + url)
    assert "immutable" in resp.headers["Cache-Control"]
    assert "max-age=31536000" in resp.headers["Cache-Control"]


def test_cross_site_post_is_rejected_same_site_allowed():
    c = _client()
    bad = c.post("/search", data={"artist": ""}, headers={"Origin": "https://evil.example"})
    assert bad.status_code == 403
    ok = c.post("/search", data={"artist": ""}, headers={"Origin": "http://localhost"})
    assert ok.status_code == 302   # empty artist -> redirect home, not blocked


def test_og_and_canonical_tags(monkeypatch):
    html = _home(monkeypatch).data.decode()
    assert '<link rel="canonical" href="http://localhost/">' in html
    assert 'property="og:title" content="Waveline · Follow the sound"' in html
    assert 'property="og:image"' in html and "waveline-app-icon-1024.png" in html
    assert 'name="twitter:card" content="summary"' in html


def test_favicon_ico_redirects_to_svg():
    resp = _client().get("/favicon.ico")
    assert resp.status_code == 301
    assert resp.headers["Location"].endswith("/static/favicon.svg")


def test_sitemap_lists_static_genres_and_analysed_artists(monkeypatch):
    monkeypatch.setattr(site_meta, "_analysed_artists",
                        lambda: [("Massive Attack", datetime.datetime(2026, 9, 1)),
                                 ("massive attack", datetime.datetime(2026, 8, 1)),
                                 ("AC/DC & Friends", datetime.datetime(2026, 7, 1))])
    site_meta._sitemap_cache.update(xml=None, at=0.0)
    resp = _client().get("/sitemap.xml")
    site_meta._sitemap_cache.update(xml=None, at=0.0)
    body = resp.data.decode()
    assert resp.status_code == 200 and resp.content_type.startswith("application/xml")
    assert "<loc>https://wearewaveline.com/genres</loc>" in body
    assert "/genre/trip-hop</loc>" in body
    assert body.count("/artist/Massive%20Attack") == 1          # de-duplicated
    assert "<lastmod>2026-09-01</lastmod>" in body
    assert "/artist/AC%2FDC%20%26%20Friends" in body            # fully escaped


def test_sitemap_survives_a_database_error(monkeypatch):
    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(site_meta, "_analysed_artists", boom)
    site_meta._sitemap_cache.update(xml=None, at=0.0)
    resp = _client().get("/sitemap.xml")
    site_meta._sitemap_cache.update(xml=None, at=0.0)
    assert resp.status_code == 200 and "/genres</loc>" in resp.data.decode()


def test_llms_txt_and_x402_manifest_and_openapi():
    c = _client()
    llms = c.get("/llms.txt").data.decode()
    assert "/api/insight?artist=" in llms and "/api/insight/preview" in llms
    manifest = json.loads(c.get("/.well-known/x402").data)
    assert manifest["version"] == 1 and manifest["x402Version"] == 2
    assert "https://wearewaveline.com/api/insight" in manifest["resources"]
    api = json.loads(c.get("/openapi.json").data)
    op = api["paths"]["/api/insight"]["get"]
    assert op["x-payment-info"]["protocols"] == ["x402"]
    assert "/api/insight/artist/{name}" in api["paths"]


def test_refresh_button_only_for_stale_analysis_when_logged_in():
    import views_artist
    old = datetime.datetime.utcnow() - datetime.timedelta(days=45)
    fresh = datetime.datetime.utcnow() - datetime.timedelta(days=2)
    assert views_artist._is_stale(old) is True
    assert views_artist._is_stale(fresh) is False
    assert views_artist._is_stale("not a date") is False
    tpl = open("templates/artist_profile.html").read()
    assert "insight_is_stale and current_user.is_authenticated" in tpl
    assert 'class="ar-refresh" method="POST" action="/search"' in tpl


def test_artist_page_has_music_group_json_ld():
    tpl = open("templates/artist_profile.html").read()
    assert '"@type": "MusicGroup"' in tpl
    assert "| tojson" in tpl

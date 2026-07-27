"""Render tests for the main page templates (blueprints + Jinja files),
with data sources mocked so no DB or network is touched.
"""

import datetime

import dashboard
import views_main
import views_news


class _Insight:
    artist = "Radiohead"
    insight = "An insight about the band."
    searched_at = datetime.datetime(2026, 7, 5, 12, 0)
    top_tracks = ["Creep", "Karma Police"]
    similar_artists = ["Muse", "Coldplay"]


def test_dashboard_page_renders(monkeypatch):
    monkeypatch.setattr(views_main, "get_dashboard_data", lambda: (
        10, 5, 2,
        [("Radiohead", 500, 500)],   # artist_plays: (name, avg, max)
        [_Insight()],                # latest_insights
        [{"name": "Blur", "image": "", "nb_fan": 100}],  # discovery
    ))
    client = dashboard.app.test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Discover what makes an artist stand out" in resp.data  # search-first hero
    assert b"Radiohead" in resp.data
    assert b"Read full insight" in resp.data  # progressive disclosure, not full article


def test_news_page_renders(monkeypatch):
    monkeypatch.setattr(views_news, "get_news_data",
                        lambda: {"articles": [
                            {"title": "Big News", "link": "https://x", "pub": "2026",
                             "source": "NME", "color": "#f00"}],
                            "releases": []})
    client = dashboard.app.test_client()
    resp = client.get("/news")
    assert resp.status_code == 200
    assert b"Big News" in resp.data


def test_all_expected_routes_registered():
    rules = {str(r) for r in dashboard.app.url_map.iter_rules()}
    for path in ["/", "/search", "/artist/<path:artist_name>", "/compare",
                 "/profile", "/news", "/register", "/login", "/u/<username>"]:
        assert path in rules, f"missing route {path}"

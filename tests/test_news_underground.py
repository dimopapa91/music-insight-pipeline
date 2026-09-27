"""/news from independent, scene-focused publications (27 Sep 2026)."""

import datetime

import pytest

import dashboard
import news_feeds
import views_news


@pytest.fixture(autouse=True)
def _fresh():
    news_feeds.clear_cache()
    news_feeds._last_good.clear()
    yield
    news_feeds.clear_cache()
    news_feeds._last_good.clear()


FEED = {"name": "Aquarium Drunkard", "home": "https://aquariumdrunkard.com", "url": "x", "scene": "psych"}

RSS = b'''<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"
 xmlns:media="http://search.yahoo.com/mrss/"><channel><title>AD</title>
<item><title>Dungen :: Vidrig V&#229;r</title><link>https://aquariumdrunkard.com/2026/09/24/dungen/</link>
<pubDate>Thu, 24 Sep 2026 13:30:00 +0000</pubDate>
<description><![CDATA[<p>On the veteran Swedish outfit's most pared down affair.</p><p>The post <a href="x">Dungen</a> first appeared on <a href="y">Aquarium Drunkard</a>.</p>]]></description>
<content:encoded><![CDATA[<p><img width="700" src="https://aquariumdrunkard.com/img.jpg"/></p>]]></content:encoded></item>
<item><title>Thumb only</title><link>https://aquariumdrunkard.com/b/</link><media:thumbnail url="https://aquariumdrunkard.com/t.png"/></item>
<item><title>No link here</title></item>
</channel></rss>'''

ATOM = b'''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>RA news</title>
<link rel="alternate" href="https://ra.co/news/1"/><published>2026-09-26T10:00:00Z</published>
<summary>Club news &amp; more</summary></entry></feed>'''


def test_rss_parse_title_link_date_excerpt_image():
    items = news_feeds.parse_feed(RSS, FEED)
    assert len(items) == 2
    a = items[0]
    assert a["title"] == "Dungen :: Vidrig Vår"
    assert a["published"] == datetime.datetime(2026, 9, 24, 13, 30)      # naive UTC
    assert a["excerpt"] == "On the veteran Swedish outfit's most pared down affair."   # boilerplate gone
    assert a["image"] == "https://aquariumdrunkard.com/img.jpg"
    assert items[1]["image"] == "https://aquariumdrunkard.com/t.png"


def test_atom_parse_and_bad_xml():
    items = news_feeds.parse_feed(ATOM, {"name": "RA", "home": "h", "url": "u", "scene": "electronic"})
    assert items[0]["link"] == "https://ra.co/news/1"
    assert items[0]["excerpt"] == "Club news & more"
    assert news_feeds.parse_feed(b"<not xml", FEED) == []


def test_per_feed_cap():
    many = b"".join(
        b"<item><title>T%d</title><link>https://x.io/%d</link></item>" % (i, i) for i in range(12))
    items = news_feeds.parse_feed(b"<rss><channel>" + many + b"</channel></rss>", FEED)
    assert len(items) == news_feeds.PER_FEED


def test_failed_feed_keeps_last_good_items(monkeypatch):
    class Ok:
        status_code = 200
        content = RSS

    monkeypatch.setattr(news_feeds.http_requests, "get", lambda *a, **k: Ok())
    first = news_feeds.fetch_feed(FEED)

    def boom(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(news_feeds.http_requests, "get", boom)
    assert news_feeds.fetch_feed(FEED) == first


def test_feed_list_is_independent_and_every_scene_has_a_source():
    names = {f["name"] for f in news_feeds.FEEDS}
    assert not names & {"NME", "Pitchfork", "The Guardian", "Rolling Stone", "Billboard"}
    assert {f["scene"] for f in news_feeds.FEEDS} == set(news_feeds.SCENES)
    assert all(f["url"].startswith("https://") for f in news_feeds.FEEDS)


def _articles():
    base = datetime.datetime(2026, 9, 27, 12, 0)
    mk = lambda i, scene, img="": {"title": f"Story {i}", "link": f"https://x.io/{i}", "excerpt": "ex",
                                   "image": img, "published": base - datetime.timedelta(hours=i),
                                   "source": "Pub", "source_home": "https://x.io", "scene": scene,
                                   "scene_label": news_feeds.SCENES[scene]}
    return [mk(0, "jazz", "https://x.io/a.jpg"), mk(1, "ambient"), mk(2, "jazz")]


def test_news_page_lead_cards_scenes_and_sources(monkeypatch):
    monkeypatch.setattr(views_news, "get_news_data", lambda: {
        "articles": _articles(), "scenes": news_feeds.SCENES, "counts": {"jazz": 2, "ambient": 1},
        "sources": [{"name": "Pub", "home": "https://x.io", "scene": "Jazz"}]})
    html = dashboard.app.test_client().get("/news").data.decode()
    assert "Beyond the charts" in html
    assert 'class="nw-lead"' in html and "Story 0" in html
    assert html.count('class="nw-card"') == 2
    assert 'href="/news?scene=jazz"' in html and 'href="/news?scene=ambient"' in html
    assert 'href="/news?scene=hiphop"' not in html            # scenes with no stories are hidden
    assert "Who we read" in html and 'rel="noopener noreferrer"' in html


def test_scene_filter(monkeypatch):
    monkeypatch.setattr(views_news, "get_news_data", lambda: {
        "articles": _articles(), "scenes": news_feeds.SCENES, "counts": {"jazz": 2, "ambient": 1},
        "sources": []})
    html = dashboard.app.test_client().get("/news?scene=ambient").data.decode()
    assert "Story 1" in html and "Story 0" not in html
    assert 'href="/news?scene=ambient" aria-current="page"' in html
    bogus = dashboard.app.test_client().get("/news?scene=<script>").data.decode()
    assert "Story 0" in bogus                                # unknown scene = all


# ── og:image fallback + background refresh ──────────────────────────

class _Page:
    def __init__(self, body, status=200):
        self.status_code = status
        self._body = body

    def iter_content(self, n):
        yield self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_og_image_found_and_cached(monkeypatch):
    news_feeds._og_cache.clear()
    calls = []

    def fake(url, **k):
        calls.append(url)
        return _Page(b'<html><head><meta property="og:image" content="https://x.io/share.jpg"></head>')
    monkeypatch.setattr(news_feeds.http_requests, "get", fake)
    assert news_feeds.og_image("https://x.io/a") == "https://x.io/share.jpg"
    assert news_feeds.og_image("https://x.io/a") == "https://x.io/share.jpg"
    assert len(calls) == 1


def test_og_image_reversed_attribute_order_and_insecure_ignored(monkeypatch):
    news_feeds._og_cache.clear()
    monkeypatch.setattr(news_feeds.http_requests, "get", lambda url, **k: _Page(
        b'<meta content="https://x.io/t.png" name="twitter:image">'))
    assert news_feeds.og_image("https://x.io/b") == "https://x.io/t.png"
    monkeypatch.setattr(news_feeds.http_requests, "get", lambda url, **k: _Page(
        b'<meta property="og:image" content="http://x.io/insecure.jpg">'))
    assert news_feeds.og_image("https://x.io/c") == ""


def test_og_image_failure_is_empty(monkeypatch):
    news_feeds._og_cache.clear()

    def boom(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(news_feeds.http_requests, "get", boom)
    assert news_feeds.og_image("https://x.io/d") == ""


def test_stale_cache_is_served_while_refreshing(monkeypatch):
    stale = {"articles": ["old"], "scenes": {}, "counts": {}, "sources": []}
    news_feeds._cache.update(data=stale, at=0.0)          # long expired
    started = []
    monkeypatch.setattr(news_feeds, "_refresh_in_background", lambda: started.append(1))
    assert news_feeds.get_news() is stale
    assert started == [1]


def test_resident_advisor_dropped():
    assert "Resident Advisor" not in {f["name"] for f in news_feeds.FEEDS}

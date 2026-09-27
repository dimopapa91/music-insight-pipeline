"""The official Waveline × nsgoods collaboration: a homepage card that
mirrors the one nsgoods shows for Waveline, plus a footer credit on every
page. No real network or database calls.
"""

import dashboard
import views_main


def _home(monkeypatch):
    monkeypatch.setattr(views_main, "get_dashboard_data", lambda: (0, 0, 0, [], [], []))
    monkeypatch.setattr(views_main, "get_plays_analysed", lambda: None)
    monkeypatch.setattr(views_main, "get_feed", lambda *a, **k: [])
    monkeypatch.setattr(dashboard, "get_site_pulse", lambda: {"recent": []})
    return dashboard.app.test_client().get("/").data.decode()


def test_homepage_shows_the_nsgoods_collaboration_card(monkeypatch):
    html = _home(monkeypatch)
    card = html[html.index('id="collaborations"'):html.index('class="wv-card wv-cta"')]
    assert "Integrations, in production" in card
    assert "Waveline × nsgoods" in card
    assert 'href="https://x402.nsgoods.org/"' in card
    assert "docs/x402.md" in card
    assert 'rel="noopener noreferrer"' in card
    assert "every 15 minutes" in card


def test_collaboration_card_sits_after_the_community_chapter(monkeypatch):
    html = _home(monkeypatch)
    assert html.index('id="chapter-04"') < html.index('id="collaborations"') < html.index('class="wv-card wv-cta"')


def test_footer_credits_nsgoods_on_every_page(monkeypatch):
    html = _home(monkeypatch)
    footer = html[html.index('class="wv-footer"'):]
    assert "in collaboration with" in footer
    assert 'href="https://x402.nsgoods.org/"' in footer


def test_collaboration_uses_the_official_logo_file_unmodified(monkeypatch):
    html = _home(monkeypatch)
    card = html[html.index('id="collaborations"'):html.index('class="wv-card wv-cta"')]
    assert "partners/nsgoods-logo-for-light-bg.svg" in card
    assert 'alt="nsgoods"' in card


def test_wording_respects_nsgoods_requests(monkeypatch):
    # nsgoods asked: no "partner"/"partnership", nothing implying Coinbase
    # approval, and make clear Dimos built the endpoint.
    html = _home(monkeypatch).replace("static/partners/", "")
    assert "partner" not in html.lower()
    assert "coinbase" not in html.lower()
    card = html[html.index('id="collaborations"'):html.index('class="wv-card wv-cta"')]
    assert "Dimos Papageorgiou built the endpoint" in card
    assert "built in collaboration with" in card

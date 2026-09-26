"""Community feed redesign: calm composer, underline tabs, side column, and
post cards that use the shared icon set. No network or database."""

import dashboard
import views_feed


def _feed(monkeypatch, posts=()):
    monkeypatch.setattr(views_feed, "get_feed", lambda *a, **k: list(posts))
    monkeypatch.setattr(dashboard, "get_site_pulse", lambda: {"recent": []})
    return dashboard.app.test_client().get("/feed?tab=latest").data.decode()


POST = dict(id=7, user_id=2, username="alice", profile_image_url="", artist="Robert Glasper",
            body="Black Radio still sounds like the future.", created_at=None,
            like_count=3, comment_count=0, liked=True, comments=[], has_more_comments=False)


def test_side_column_links_every_genre(monkeypatch):
    import services
    html = _feed(monkeypatch)
    aside = html[html.index('class="cm-aside"'):]
    for g in services.GENRES:
        assert f'href="/genre/{g["slug"]}"' in aside
    assert "House rules" in aside


def test_underline_tabs_keep_their_contract(monkeypatch):
    html = _feed(monkeypatch)
    assert 'aria-current="page">Latest<' in html


def test_post_card_uses_sprite_icons_not_glyphs(monkeypatch):
    html = _feed(monkeypatch, [POST])
    card = html[html.index('<article class="wv-post">'):html.index("</article>")]
    assert "#wv-i-note" in card and "#wv-i-heart" in card and "#wv-i-comment" in card
    assert "♪" not in card
    assert "is-liked" in card


def test_guest_sees_the_invitation_not_the_composer(monkeypatch):
    html = _feed(monkeypatch)
    assert "Join the conversation" in html
    assert 'id="cm-compose"' not in html

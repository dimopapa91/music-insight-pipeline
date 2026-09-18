"""Tests for the Waveline Signal System redesign: shared navigation shell,
command palette, consolidated mini-player, and the new /about route."""

import datetime

import dashboard
import views_main


class _Insight:
    artist = "Radiohead"
    insight = "An insight about the band."
    searched_at = datetime.datetime(2026, 7, 5, 12, 0)
    top_tracks = ["Creep", "Karma Police"]
    similar_artists = ["Muse", "Coldplay"]


def _dashboard_client(monkeypatch):
    monkeypatch.setattr(views_main, "get_dashboard_data", lambda: (
        10, 5, 2, [("Radiohead", 500, 500)], [_Insight()],
        [{"name": "Blur", "image": "", "nb_fan": 100}],
    ))
    monkeypatch.setattr(views_main, "get_feed", lambda *a, **k: [])
    return dashboard.app.test_client()


# ── Desktop dock navigation: labelled links, not icon-only ──

def test_desktop_nav_has_labelled_links(monkeypatch):
    client = _dashboard_client(monkeypatch)
    resp = client.get("/")
    html = resp.data.decode()
    assert '<nav class="wv-nav"' in html
    for label in ["Home", "Discover", "Feed", "Taste"]:
        assert f">{label}<" in html


def test_desktop_nav_no_longer_shows_old_labels(monkeypatch):
    client = _dashboard_client(monkeypatch)
    html = client.get("/").data.decode()
    nav_start = html.index('<nav class="wv-nav"')
    nav_end = html.index("</nav>", nav_start)
    primary_nav = html[nav_start:nav_end]
    # Phase 5: Discover is now a real, intentional primary-nav destination.
    assert ">Community<" not in primary_nav
    # Compare stays in the desktop More menu.
    assert ">Compare<" not in primary_nav
    # News was promoted back out of More to a top-level tab — it was too
    # buried to find in a dropdown.
    assert ">News<" in primary_nav


def test_desktop_more_menu_contains_compare(monkeypatch):
    client = _dashboard_client(monkeypatch)
    html = client.get("/").data.decode()
    assert 'id="wv-navmenu-trigger"' in html
    assert 'aria-haspopup="menu"' in html
    panel_start = html.index('id="wv-navmenu-panel"')
    panel_end = html.index("</div>", panel_start)
    panel = html[panel_start:panel_end]
    assert 'href="/compare"' in panel
    assert ">Compare<" in panel
    # News is no longer here; it's a primary-nav tab.
    assert 'href="/news"' not in panel


# ── Mobile bottom navigation ──

def test_mobile_bottom_nav_present(monkeypatch):
    client = _dashboard_client(monkeypatch)
    html = client.get("/").data.decode()
    assert 'class="wv-bottomnav"' in html
    bottomnav_start = html.index('class="wv-bottomnav"')
    bottomnav_end = html.index("</nav>", bottomnav_start)
    bottomnav = html[bottomnav_start:bottomnav_end]
    # Phase 8.2: bottom-nav labels are visually removed (Instagram-style
    # icon-only bar) but stay accessible via visually-hidden text, except
    # the fifth item, which became an Account/avatar control with no
    # separate "More" text at all (see test_mobile_tabbar_hotfix.py) — so
    # it's checked for its accessible name instead of visible text.
    for label in ["Home", "Discover", "Feed", "Taste"]:
        assert f">{label}<" in bottomnav
    assert 'id="wv-more-btn"' in bottomnav
    assert "aria-label=" in bottomnav[bottomnav.index('id="wv-more-btn"'):bottomnav.index('id="wv-more-btn"') + 400]
    # Phase 5: Compare gave up its permanent bottom-nav slot to Discover —
    # it now lives in the mobile More panel instead (see test below).
    assert ">Compare<" not in bottomnav
    assert ">Community<" not in bottomnav
    # persistent artist-search shortcut reachable from the mobile shell
    assert 'id="wv-search-trigger-mobile"' in html


def test_mobile_more_panel_has_secondary_actions(monkeypatch):
    client = _dashboard_client(monkeypatch)
    html = client.get("/").data.decode()
    assert 'id="wv-morepanel"' in html
    assert 'href="/compare"' in html
    assert 'href="/news"' in html
    assert 'href="/about"' in html


# ── Command palette ──

def test_command_palette_markup_present_and_hidden(monkeypatch):
    client = _dashboard_client(monkeypatch)
    html = client.get("/").data.decode()
    assert 'id="wv-palette-overlay"' in html
    assert 'id="wv-palette-overlay" hidden' in html
    assert 'aria-modal="true"' in html
    assert 'id="wv-palette-input"' in html


# ── Consolidated persistent mini-player (single instance, not per-page) ──

def test_mini_player_appears_once_globally(monkeypatch):
    client = _dashboard_client(monkeypatch)
    html = client.get("/").data.decode()
    assert html.count('id="wv-player"') == 1
    assert html.count('id="wv-audio"') == 1


def test_artist_page_does_not_duplicate_player(monkeypatch):
    import views_artist

    def fake_cursor(*a, **k):
        raise RuntimeError("no db in unit test")

    # Force the artist route straight to its error path (still extends base.html,
    # which is what we're checking: exactly one player instance survives).
    monkeypatch.setattr(views_artist, "db_cursor", fake_cursor)
    client = dashboard.app.test_client()
    resp = client.get("/artist/Radiohead")
    assert resp.status_code == 500
    assert resp.data.decode().count('id="wv-player"') == 1


# ── Shared error template (no more inline hand-rolled HTML documents) ──

def test_artist_error_uses_shared_shell(monkeypatch):
    import views_artist

    def fake_cursor(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(views_artist, "db_cursor", fake_cursor)
    client = dashboard.app.test_client()
    resp = client.get("/artist/Some Unknown Artist")
    assert resp.status_code == 500
    html = resp.data.decode()
    assert 'class="wv-dock"' in html          # shared nav shell present
    assert 'id="wv-morepanel"' in html        # full shared shell, not a bespoke error doc
    assert "Something went wrong" in html


# ── /about (How Waveline works) ──

def test_about_page_renders():
    client = dashboard.app.test_client()
    resp = client.get("/about")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "How Waveline works" in html or "How Waveline Works" in html
    assert "PostgreSQL" in html
    assert "Claude" in html


def test_about_linked_from_footer_and_more_panel(monkeypatch):
    client = _dashboard_client(monkeypatch)
    html = client.get("/").data.decode()
    assert html.count('href="/about"') >= 2


# ── Static assets for the new signal system are actually served ──

def test_new_static_js_assets_are_served():
    client = dashboard.app.test_client()
    for path in ["/static/js/nav.js", "/static/js/command-palette.js",
                 "/static/js/player.js", "/static/js/signal-scene.js"]:
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} not served"

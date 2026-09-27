"""Header cleanup (27 Sep 2026): W mark in the search trigger, no ⌘K hint,
avatar-only account button, no desktop "More" menu, thinner outlines."""

import re

from test_auth_navigation import _dashboard_client, _login


def _dock(monkeypatch, login=True):
    client = _dashboard_client(monkeypatch)
    if login:
        _login(client, monkeypatch)
    html = client.get("/").data.decode()
    return html[html.index('<header class="wv-header"'):html.index("</header>")]


def test_search_trigger_uses_waveline_mark_and_no_shortcut_hint(monkeypatch):
    dock = _dock(monkeypatch)
    trigger = dock[dock.index('id="wv-search-trigger"'):]
    trigger = trigger[:trigger.index("</button>")]
    assert "brand/waveline-mark.svg" in trigger
    assert "brand/waveline-mark-on-light.svg" in trigger
    assert "8984" not in trigger and "⌘" not in trigger and "<kbd" not in trigger
    assert 'aria-label="Search"' in trigger


def test_account_button_shows_only_the_avatar(monkeypatch):
    dock = _dock(monkeypatch)
    trigger = dock[dock.index('id="wv-profile-trigger"'):]
    trigger = trigger[:trigger.index("</button>")]
    visible = re.sub(r'aria-label="[^"]*"', "", trigger)
    assert "@" not in visible
    assert "wv-profile-avatar" in trigger
    assert "wv-profilemenu-caret" not in trigger
    assert 'aria-label="Account menu, @' in trigger  # still named for screen readers


def test_no_desktop_more_menu_for_anyone(monkeypatch):
    assert "wv-navmenu" not in _dock(monkeypatch, login=True)
    assert "wv-navmenu" not in _dock(monkeypatch, login=False)


def test_header_outlines_use_the_subtle_border():
    css = open("static/css/waveline.css").read()
    for sel in (".wv-iconbtn {", ".wv-profilemenu-trigger {", ".wv-searchbtn {"):
        rule = css[css.index(sel):]
        rule = rule[:rule.index("}")]
        assert "1px solid var(--wv-border);" in rule, sel

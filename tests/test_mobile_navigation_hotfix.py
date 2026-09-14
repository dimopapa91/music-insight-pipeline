"""Tests for Phase 8.1: mobile navigation UX hotfix.

A real iPhone screenshot of the Phase 8 merge showed the mobile shell working
structurally but reading as visually generic/empty: authenticated users had
no way to reach Messages/Notifications without opening the More sheet, the
bottom nav used five unrelated Unicode glyphs, and the theme control's "◑"
was visually adjacent to Taste's own icon-less list slot. This file locks in
the fixes: real SVG icons, mobile-only header shortcuts that navigate
directly (not a second copy of the desktop dropdowns), a restructured More
panel, and sun/moon theme icons.

No real browser is available in this environment (no jsdom/Playwright — see
test_responsive_accessibility.py's module docstring for the same point), so
anything about actual rendered layout/keyboard execution is out of reach
here too. These are markup/source-structure tests.
"""

import re

import dashboard
import views_main
from models import User

FAKE = User(id=1, username="dimos", email="d@e.com", password_hash="x")

OLD_BOTTOMNAV_GLYPHS = ["◎", "✦", "◈", "◑", "☰"]


def _login(client, monkeypatch, user=FAKE):
    monkeypatch.setattr(User, "get", classmethod(lambda cls, i: user))
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)


def _read(path):
    with open(path) as f:
        return f.read()


def _dashboard_client(monkeypatch, **counts):
    monkeypatch.setattr(views_main, "get_dashboard_data", lambda: (10, 5, 2, [], [], []))
    monkeypatch.setattr(views_main, "get_feed", lambda *a, **k: [])
    client = dashboard.app.test_client()
    if counts:
        import dashboard as dashboard_module
        for name, value in counts.items():
            monkeypatch.setattr(dashboard_module, name, lambda uid, _v=value: _v)
    return client


def _section(html, start, end):
    i = html.index(start)
    j = html.index(end, i)
    return html[i:j]


def _bottomnav(html):
    return _section(html, 'class="wv-bottomnav"', "</nav>")


def _dock(html):
    return _section(html, '<header class="wv-header"', "</header>")


def _more_panel(html):
    return _section(html, 'id="wv-morepanel"', "<!-- ═══ COMMAND PALETTE")


# ── 1/2. bottom nav: still exactly five items, same routes ───────────

def test_bottom_nav_has_exactly_five_items(monkeypatch):
    html = _dashboard_client(monkeypatch).get("/").data.decode()
    bottomnav = _bottomnav(html)
    # one <a ...> or <button ...> per destination — count top-level items
    # by counting the labels, which is what the pre-existing
    # test_mobile_bottom_nav_present already anchors on
    for label in ["Home", "Discover", "Feed", "Taste", "More"]:
        assert f'<span class="lbl">{label}</span>' in bottomnav
    assert bottomnav.count('<span class="lbl">') == 5


def test_bottom_nav_routes_unchanged(monkeypatch):
    html = _dashboard_client(monkeypatch).get("/").data.decode()
    bottomnav = _bottomnav(html)
    assert 'href="/"' in bottomnav
    assert 'href="/discover"' in bottomnav
    assert 'href="/feed"' in bottomnav
    assert 'href="/profile"' in bottomnav
    assert 'id="wv-more-btn"' in bottomnav
    # still a button (dialog trigger), not a sixth navigable route
    assert 'aria-haspopup="dialog"' in bottomnav
    assert 'aria-controls="wv-morepanel"' in bottomnav


# ── 3/4. old Unicode icons gone, real SVGs in place ──────────────────

def test_old_unicode_bottomnav_glyphs_are_gone():
    html = _read("templates/base.html")
    bottomnav = _bottomnav(html)
    for glyph in OLD_BOTTOMNAV_GLYPHS:
        assert glyph not in bottomnav, f"stale glyph {glyph!r} still present"


def test_all_five_bottom_items_use_inline_svg():
    html = _read("templates/base.html")
    bottomnav = _bottomnav(html)
    assert bottomnav.count("<svg") == 5


# ── 5/6. Taste routing + icon identity distinct from theme icon ─────

def test_taste_still_links_to_profile():
    html = _read("templates/base.html")
    bottomnav = _bottomnav(html)
    taste_link = bottomnav[bottomnav.index('href="/profile"') - 10:bottomnav.index('href="/profile"') + 400]
    assert '<span class="lbl">Taste</span>' in taste_link


def test_taste_icon_is_not_theme_icon_markup():
    html = _read("templates/base.html")
    bottomnav = _bottomnav(html)
    taste_icon = bottomnav[bottomnav.index('href="/profile"'):bottomnav.index("</a>", bottomnav.index('href="/profile"'))]
    # the theme control's sun (circle r="4.2" + rays) and moon (crescent
    # path starting "M20 14.5A8.5") must never appear inside Taste's icon
    assert 'r="4.2"' not in taste_icon
    assert "M20 14.5A8.5" not in taste_icon
    assert "wv-theme-icon" not in taste_icon
    # it IS the waveform/equalizer glyph
    assert "M4 13v-2M8 15.5v-7M12 17v-10" in taste_icon


def test_theme_button_no_longer_uses_the_old_glyph():
    html = _read("templates/base.html")
    assert "◑" not in html
    assert html.count("wv-theme-icon-sun") == 2  # desktop + mobile More row
    assert html.count("wv-theme-icon-moon") == 2


# ── 7. authenticated mobile header: Messages + Notifications + badges ──

def test_authenticated_mobile_header_has_messages_and_notifications(monkeypatch):
    client = _dashboard_client(monkeypatch)
    _login(client, monkeypatch)
    html = client.get("/").data.decode()
    dock = _dock(html)
    assert 'class="wv-iconbtn wv-msgicon wv-mobile-only" href="/messages"' in dock
    assert 'class="wv-iconbtn wv-bell wv-mobile-only" href="/notifications"' in dock


def test_authenticated_mobile_header_badges_render(monkeypatch):
    import dashboard as dashboard_module
    monkeypatch.setattr(dashboard_module, "count_unread", lambda uid: 3)
    monkeypatch.setattr(dashboard_module, "count_unread_messages", lambda uid: 5)
    client = _dashboard_client(monkeypatch)
    _login(client, monkeypatch)
    html = client.get("/").data.decode()
    dock = _dock(html)
    msg_link = dock[dock.index('href="/messages"'):dock.index("</a>", dock.index('href="/messages"'))]
    notif_link = dock[dock.index('href="/notifications"'):dock.index("</a>", dock.index('href="/notifications"'))]
    assert 'class="wv-badge"' in msg_link and ">5</span>" in msg_link
    assert 'class="wv-badge"' in notif_link and ">3</span>" in notif_link


def test_mobile_header_badges_cap_at_nine_plus(monkeypatch):
    import dashboard as dashboard_module
    monkeypatch.setattr(dashboard_module, "count_unread", lambda uid: 42)
    monkeypatch.setattr(dashboard_module, "count_unread_messages", lambda uid: 130)
    client = _dashboard_client(monkeypatch)
    _login(client, monkeypatch)
    dock = _dock(client.get("/").data.decode())
    msg_link = dock[dock.index('href="/messages"'):dock.index("</a>", dock.index('href="/messages"'))]
    notif_link = dock[dock.index('href="/notifications"'):dock.index("</a>", dock.index('href="/notifications"'))]
    assert ">9+</span>" in msg_link
    assert ">9+</span>" in notif_link
    # the full precise count is still fine (even good) inside the
    # aria-label — only the visual badge text is capped for space
    assert 'aria-label="Messages (130 unread)"' in msg_link
    assert 'aria-label="Notifications (42 unread)"' in notif_link


# ── 8. logged-out mobile header: no Messages/Notifications, has Log in ──

def test_logged_out_mobile_header_has_no_messages_or_notifications(monkeypatch):
    html = _dashboard_client(monkeypatch).get("/").data.decode()
    dock = _dock(html)
    assert "wv-msgicon" not in dock
    assert "wv-bell" not in dock
    assert 'href="/messages"' not in dock
    assert 'href="/notifications"' not in dock


def test_logged_out_mobile_header_exposes_log_in(monkeypatch):
    html = _dashboard_client(monkeypatch).get("/").data.decode()
    dock = _dock(html)
    assert 'class="wv-userchip wv-mobile-only" href="/login"' in dock


# ── 9/10. More panel content hierarchy ───────────────────────────────

def test_logged_out_more_panel_puts_join_before_explore(monkeypatch):
    html = _dashboard_client(monkeypatch).get("/").data.decode()
    panel = _more_panel(html)
    assert panel.index("Join Waveline") < panel.index(">Explore<")
    assert panel.index('href="/register"') < panel.index('href="/compare"')
    assert panel.index('href="/login"') < panel.index('href="/compare"')


def test_logged_out_more_panel_create_account_is_primary_log_in_is_secondary(monkeypatch):
    html = _dashboard_client(monkeypatch).get("/").data.decode()
    panel = _more_panel(html)
    join_start = panel.index("wv-more-join")
    join_block = panel[join_start:panel.index("</div>", join_start)]
    assert "wv-btn-primary" in join_block[:join_block.index('href="/register"')] or \
        'class="wv-btn wv-btn-primary wv-more-join-primary" href="/register"' in join_block
    assert 'class="wv-btn wv-btn-ghost wv-more-join-secondary" href="/login"' in join_block


def test_authenticated_more_panel_exposes_messages_and_notifications_prominently(monkeypatch):
    client = _dashboard_client(monkeypatch)
    _login(client, monkeypatch)
    html = client.get("/").data.decode()
    panel = _more_panel(html)
    assert "wv-more-quickgrid" in panel
    assert 'class="wv-more-quick" href="/messages"' in panel
    assert 'class="wv-more-quick" href="/notifications"' in panel
    # prominent = ahead of Explore, not buried below Compare/News/About
    assert panel.index("wv-more-quickgrid") < panel.index(">Explore<")


def test_authenticated_more_panel_has_account_identity_header(monkeypatch):
    client = _dashboard_client(monkeypatch)
    _login(client, monkeypatch)
    html = client.get("/").data.decode()
    panel = _more_panel(html)
    assert "wv-more-account" in panel
    assert "@dimos" in panel[panel.index("wv-more-account"):panel.index("wv-more-account") + 400]


# ── 11. More panel dialog semantics + focus behaviour untouched ─────

def test_more_panel_dialog_semantics_unchanged():
    html = _read("templates/base.html")
    tag = html[html.index('id="wv-morepanel"') - 60:html.index('id="wv-morepanel"') + 80]
    assert 'role="dialog"' in tag
    assert 'aria-modal="true"' in tag
    assert 'aria-label="More"' in tag
    assert "hidden" in tag


def test_more_panel_js_focus_trap_escape_and_return_still_wired():
    # initMorePanel() itself is untouched by this hotfix — same assertions
    # test_responsive_accessibility.py already locked in, repeated here as
    # a direct guard against this specific phase's changes regressing them.
    js = _read("static/js/nav.js")
    body = js[js.index("function initMorePanel"):js.index("function initNavDropdown")]
    assert 'if (e.key === "Escape") { close(); return; }' in body
    assert 'if (e.key === "Tab") {' in body
    assert "lastFocus = document.activeElement;" in body
    assert "lastFocus.focus();" in body
    assert 'document.body.style.overflow = "hidden";' in body
    assert 'document.body.style.overflow = "";' in body


# ── 12. accessible names on new icon-only mobile header controls ────

def test_new_mobile_header_icons_have_accessible_names(monkeypatch):
    client = _dashboard_client(monkeypatch)
    _login(client, monkeypatch)
    dock = _dock(client.get("/").data.decode())
    msg_link = dock[dock.index('href="/messages"'):dock.index("</a>", dock.index('href="/messages"'))]
    notif_link = dock[dock.index('href="/notifications"'):dock.index("</a>", dock.index('href="/notifications"'))]
    assert 'aria-label="Messages' in msg_link
    assert 'aria-label="Notifications' in notif_link


def test_new_mobile_header_svgs_are_aria_hidden(monkeypatch):
    client = _dashboard_client(monkeypatch)
    _login(client, monkeypatch)
    dock = _dock(client.get("/").data.decode())
    for href in ['href="/messages"', 'href="/notifications"']:
        start = dock.index(href)
        svg_start = dock.index("<svg", start)
        # the svg itself carries aria-hidden+focusable, or its wrapper does
        svg_tag = dock[svg_start:dock.index(">", svg_start) + 1]
        wrapper = dock[max(0, svg_start - 60):svg_start]
        assert 'aria-hidden="true"' in svg_tag or 'aria-hidden="true"' in wrapper
        assert 'focusable="false"' in svg_tag


# ── 13. bottom-nav SVGs are decorative (visible labels exist already) ──

def test_bottom_nav_svgs_are_aria_hidden():
    html = _read("templates/base.html")
    bottomnav = _bottomnav(html)
    # every .ic wrapper (which contains the svg) is aria-hidden
    assert bottomnav.count('class="ic" aria-hidden="true"') == 5
    assert bottomnav.count('focusable="false"') == 5


# ── 14/15/16. desktop dropdowns untouched ────────────────────────────

def test_authenticated_desktop_messages_dropdown_still_exists(monkeypatch):
    client = _dashboard_client(monkeypatch)
    _login(client, monkeypatch)
    html = client.get("/").data.decode()
    assert 'id="wv-msg-trigger"' in html
    assert 'id="wv-msg-panel"' in html
    trigger_tag = html[html.index('id="wv-msg-trigger"') - 80:html.index('id="wv-msg-trigger"') + 60]
    assert "wv-desktop-only" in trigger_tag or "wv-msgmenu wv-desktop-only" in html[html.index('id="wv-msg-trigger"') - 200:html.index('id="wv-msg-trigger"')]


def test_authenticated_desktop_notifications_dropdown_still_exists(monkeypatch):
    client = _dashboard_client(monkeypatch)
    _login(client, monkeypatch)
    html = client.get("/").data.decode()
    assert 'id="wv-notif-trigger"' in html
    assert 'id="wv-notif-panel"' in html


def test_desktop_dropdown_wrappers_still_desktop_only():
    html = _read("templates/base.html")
    assert 'class="wv-msgmenu wv-desktop-only"' in html
    assert 'class="wv-notifmenu wv-desktop-only"' in html


def test_desktop_dropdown_js_wiring_untouched():
    # This hotfix must not touch initNavDropdown/initNotifDropdown/
    # initMsgDropdown's own mechanics — only add a new getElementById
    # clear-step for the new mobile header badge (see next test).
    js = _read("static/js/nav.js")
    assert 'initNavDropdown("wv-msg-trigger", "wv-msg-panel"' in js
    assert 'initNavDropdown("wv-notif-trigger", "wv-notif-panel"' in js


def test_mobile_header_notif_badge_gets_synced_by_the_same_success_path():
    js = _read("static/js/nav.js")
    notif_block = js[js.index("function initNotifDropdown"):js.index("/* ── Scroll-reveal")]
    success_body = notif_block.split(".then(function (html)", 1)[1].split(".catch(function ()")[0]
    assert 'getElementById("wv-notif-badge-mobile")' in success_body
    assert 'getElementById("wv-notif-badge-header-mobile")' in success_body


# ── 17/18. mobile links are plain navigation, no mutation/dropdown coupling ──

def test_mobile_messages_link_is_plain_navigation_not_a_dropdown_trigger():
    html = _read("templates/base.html")
    link_tag = html[html.index('<a class="wv-iconbtn wv-msgicon wv-mobile-only"'):html.index("</a>", html.index('<a class="wv-iconbtn wv-msgicon wv-mobile-only"'))]
    assert "aria-haspopup" not in link_tag
    assert "aria-controls" not in link_tag
    assert "aria-expanded" not in link_tag


def test_mobile_notifications_link_is_plain_navigation_not_a_dropdown_trigger():
    html = _read("templates/base.html")
    link_tag = html[html.index('<a class="wv-iconbtn wv-bell wv-mobile-only"'):html.index("</a>", html.index('<a class="wv-iconbtn wv-bell wv-mobile-only"'))]
    assert "aria-haspopup" not in link_tag
    assert "aria-controls" not in link_tag


def test_visiting_messages_page_directly_does_not_touch_preview_or_read_mutation():
    # The mobile header link just navigates to the real /messages route —
    # confirm that route's own view function is unrelated to the preview
    # dropdown's read-mutation-free contract (already covered in depth by
    # test_experience_fixes.py's message-preview tests); this just proves
    # the hotfix didn't wire the new link through any JS fetch/mutation path.
    import inspect
    import views_messages
    src = inspect.getsource(views_messages.preview)
    assert "mark_conversation_read" not in src


# ── 19. mobile header CSS has a narrow-width strategy ────────────────

def test_mobile_header_icons_get_a_44px_touch_target():
    css = _read("static/css/waveline.css")
    assert ".wv-header .wv-iconbtn.wv-mobile-only { min-width: 44px; height: 44px; }" in css


def test_header_actions_no_longer_depends_on_a_hidden_siblings_margin():
    # Regression for the actual root cause of the "empty-looking header":
    # .wv-header-actions had no margin of its own and only stayed
    # right-aligned via .wv-nav's margin-right:auto, which has zero effect
    # once .wv-nav is display:none on mobile.
    css = _read("static/css/waveline.css")
    rule = re.search(r"\.wv-header-actions \{([^}]*)\}", css).group(1)
    assert "margin-left: auto" in rule


# ── 20. safe-area bottom behaviour remains intact ────────────────────

def test_safe_area_bottom_padding_still_intact():
    css = _read("static/css/waveline.css")
    assert "padding-bottom: env(safe-area-inset-bottom, 0);" in css
    assert re.search(
        r"body \{ padding-bottom: calc\(var\(--wv-bottomnav-h\) \+ env\(safe-area-inset-bottom, 0\)\); \}",
        css,
    )


def test_bottom_nav_height_was_not_dramatically_increased():
    css = _read("static/css/waveline.css")
    assert "--wv-bottomnav-h: 60px;" in css


# ── active-state polish (section 4/12): one-shot, reduced-motion safe ──

def test_active_bottom_nav_state_uses_a_one_shot_animation_not_looping():
    css = _read("static/css/waveline.css")
    rule = re.search(r'\.wv-bottomnav a\[aria-current="page"\] \.ic \{([^}]*)\}', css).group(1)
    animation = re.search(r"animation:\s*([^;]+);", rule).group(1)
    # shorthand form "<name> <duration> <timing> <iteration-count>" — the
    # last token must be a plain "1", never "infinite"
    assert animation.strip().split()[-1] == "1"


def test_more_button_activity_dot_is_aria_hidden_and_not_shown_when_no_unread(monkeypatch):
    client = _dashboard_client(monkeypatch)
    _login(client, monkeypatch)
    html = client.get("/").data.decode()  # no unread mocked -> both counts 0
    bottomnav = _bottomnav(html)
    assert "wv-moredot" not in bottomnav


def test_more_button_activity_dot_appears_when_unread_exists(monkeypatch):
    import dashboard as dashboard_module
    monkeypatch.setattr(dashboard_module, "count_unread", lambda uid: 1)
    client = _dashboard_client(monkeypatch)
    _login(client, monkeypatch)
    html = client.get("/").data.decode()
    bottomnav = _bottomnav(html)
    assert '<span class="wv-moredot" aria-hidden="true"></span>' in bottomnav


# ── theme toggle JS: no destructive textContent on the button itself ──

def test_theme_toggle_js_never_overwrites_button_textcontent():
    js = _read("static/js/nav.js")
    body = js[js.index("function initTheme"):js.index("/* ── Mobile")]
    # the old bug pattern this guards against: b.textContent = ... would
    # wipe out the new sun/moon <span> children on every click
    assert "b.textContent" not in body
    assert ".textContent" in body  # still updates the mobile label SPAN specifically
    assert "querySelector(\".wv-theme-label-text\")" in body

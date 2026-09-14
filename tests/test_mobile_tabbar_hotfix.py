"""Tests for Phase 8.2: mobile tab bar + account sheet hotfix.

A real iPhone SCREEN RECORDING of the merged Phase 8.1 deployment showed the
account sheet ("More") never actually opening — the JS was correct
(`panel.hidden = false`), but the mobile CSS never restored a non-`none`
`display` value for the un-hidden case, so the panel stayed invisible
regardless of the `hidden` attribute. This file's first test resolves real
CSS specificity for the handful of selectors involved to prove the fix,
rather than just checking the JS (which was never the bug).

The rest of this file covers the Instagram-inspired bottom-bar redesign:
icon-only tabs (labels stay accessible via .wv-sr-only, never removed from
the DOM), a real house icon for Home, and the "More" trigger becoming an
Account/avatar control.

No real browser is available in this environment (no jsdom/Playwright), so
— consistent with every prior phase's test file in this repo — these are
markup/CSS/JS source-structure tests, not a rendered/visual/keyboard check.
"""

import re

import dashboard
import views_main
from models import User

FAKE = User(id=1, username="dimos", email="d@e.com", password_hash="x")


def _login(client, monkeypatch, user=FAKE):
    monkeypatch.setattr(User, "get", classmethod(lambda cls, i: user))
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)


def _read(path):
    with open(path) as f:
        return f.read()


def _dashboard_client(monkeypatch):
    monkeypatch.setattr(views_main, "get_dashboard_data", lambda: (10, 5, 2, [], [], []))
    monkeypatch.setattr(views_main, "get_feed", lambda *a, **k: [])
    return dashboard.app.test_client()


def _section(html, start, end):
    i = html.index(start)
    j = html.index(end, i)
    return html[i:j]


def _bottomnav(html):
    return _section(html, 'class="wv-bottomnav"', "</nav>")


def _more_panel(html):
    return _section(html, 'id="wv-morepanel"', "<!-- ═══ COMMAND PALETTE")


# ── 1/17. THE ACTUAL BUG: CSS cascade, not JS ─────────────────────────

def _specificity(selector):
    """(ids, classes+attributes+pseudo-classes, types) — scoped to the
    handful of selector shapes this file actually uses for .wv-morepanel
    (bare class, attribute selector, :not() wrapping an attribute
    selector). Not a general CSS selector parser."""
    ids = len(re.findall(r"#[\w-]+", selector))
    classlike = len(re.findall(r"\.[\w-]+", selector)) + len(re.findall(r"\[[^\]]+\]", selector))
    return (ids, classlike, 0)


def test_morepanel_display_cascade_resolves_visible_when_not_hidden():
    css = _read("static/css/waveline.css")

    global_rule = re.search(
        r"\.wv-bottomnav,\s*\.wv-moretrigger,\s*\.wv-morepanel\s*\{\s*display:\s*([a-z]+);\s*\}", css
    )
    assert global_rule, "expected the global default-hidden rule"
    global_spec = _specificity(".wv-morepanel")

    mobile_block = re.search(r"@media \(max-width: 860px\) \{(.*?)\n\}\n", css, re.S).group(1)

    hidden_rule = re.search(r"\.wv-morepanel\[hidden\]\s*\{\s*display:\s*([a-z]+);\s*\}", mobile_block)
    assert hidden_rule, "expected .wv-morepanel[hidden] inside the mobile block"

    not_hidden_rule = re.search(r"\.wv-morepanel:not\(\[hidden\]\)\s*\{\s*display:\s*([a-z]+);\s*\}", mobile_block)
    assert not_hidden_rule, (
        "no rule restores a non-none display for .wv-morepanel when the hidden "
        "attribute is absent on mobile — this is exactly the real bug: the only "
        "applicable rule would resolve to display:none regardless of the "
        "hidden attribute, which is why tapping the account button did nothing "
        "on the real iPhone despite initMorePanel() correctly clearing it"
    )

    # both scoped rules must actually outrank the bare global default in
    # real CSS specificity terms, or the fix would silently never apply
    assert _specificity(".wv-morepanel[hidden]") > global_spec
    assert _specificity(".wv-morepanel:not([hidden])") > global_spec

    assert global_rule.group(1) == "none"
    assert hidden_rule.group(1) == "none"
    assert not_hidden_rule.group(1) != "none"


def test_hidden_state_still_resolves_to_none():
    # The fix must not accidentally make the panel permanently visible —
    # [hidden] must still win when the attribute IS present.
    css = _read("static/css/waveline.css")
    mobile_block = re.search(r"@media \(max-width: 860px\) \{(.*?)\n\}\n", css, re.S).group(1)
    assert ".wv-morepanel[hidden] { display: none; }" in mobile_block


def test_this_was_not_a_javascript_bug():
    # initMorePanel() already did the right thing before this hotfix —
    # confirm the fix didn't need to (and didn't) touch it.
    js = _read("static/js/nav.js")
    body = js[js.index("function initMorePanel"):js.index("function initNavDropdown")]
    assert "panel.hidden = false;" in body
    assert "panel.hidden = true;" in body


# ── 2/3. label removal, accessible names preserved ───────────────────

def test_bottom_nav_labels_are_visually_hidden_not_removed():
    html = _read("templates/base.html")
    bottomnav = _bottomnav(html)
    for label in ["Home", "Discover", "Feed", "Taste"]:
        assert f'<span class="lbl wv-sr-only">{label}</span>' in bottomnav


def test_no_visible_text_labels_remain_in_the_bottom_bar():
    # The old .wv-bottomnav .lbl typography rule (font/letter-spacing/etc.)
    # is gone entirely — nothing styles .lbl as visible text any more, so
    # there's no competing rule left that could defeat .wv-sr-only's own
    # clipping technique (the actual, single source of the hiding).
    css = _read("static/css/waveline.css")
    assert ".wv-bottomnav .lbl" not in css
    sr_only_rule = re.search(r"\.wv-sr-only \{([^}]*)\}", css)
    assert sr_only_rule, "expected the shared .wv-sr-only utility to exist"
    assert "position: absolute" in sr_only_rule.group(1)


def test_more_label_text_is_gone_replaced_by_aria_label():
    html = _read("templates/base.html")
    bottomnav = _bottomnav(html)
    assert "<span class=\"lbl" not in bottomnav[bottomnav.index('id="wv-more-btn"'):]
    assert 'aria-label="{{' in bottomnav[bottomnav.index('id="wv-more-btn"'):bottomnav.index('id="wv-more-btn"') + 300]


# ── 4/6. Home: real house icon, old signal/ripple gone ───────────────

def test_home_uses_a_house_icon():
    html = _read("templates/base.html")
    bottomnav = _bottomnav(html)
    home_block = bottomnav[bottomnav.index('href="/"'):bottomnav.index("</a>", bottomnav.index('href="/"'))]
    assert "wv-home-icon-outline" in home_block
    assert "wv-home-icon-filled" in home_block


def test_old_ripple_signal_home_markup_is_gone():
    html = _read("templates/base.html")
    bottomnav = _bottomnav(html)
    home_block = bottomnav[bottomnav.index('href="/"'):bottomnav.index("</a>", bottomnav.index('href="/"'))]
    # the old Phase 8.1 icon: a filled dot + two concentric stroked arcs
    assert "cy=\"18\" r=\"1.6\"" not in home_block
    assert "a5.6 5.6 0 0 1 7.6 0" not in home_block
    assert "a10 10 0 0 1 14 0" not in home_block


def test_home_active_state_swaps_outline_for_filled_not_a_colour_hack():
    css = _read("static/css/waveline.css")
    mobile_block = re.search(r"@media \(max-width: 860px\) \{(.*?)\n\}\n", css, re.S).group(1)
    assert ".wv-home-icon-filled { display: none; }" in mobile_block
    assert re.search(r'\.wv-bottomnav a\[aria-current="page"\] \.wv-home-icon-outline \{ display: none; \}', mobile_block)
    assert re.search(r'\.wv-bottomnav a\[aria-current="page"\] \.wv-home-icon-filled \{ display: inline-flex; \}', mobile_block)


# ── 7/8/9/10. Discover/Feed/Taste routes + icon concepts unchanged ───

def test_discover_feed_taste_routes_unchanged():
    html = _read("templates/base.html")
    bottomnav = _bottomnav(html)
    assert 'href="/discover"' in bottomnav
    assert 'href="/feed"' in bottomnav
    assert 'href="/profile"' in bottomnav


def test_taste_keeps_the_waveform_concept():
    html = _read("templates/base.html")
    bottomnav = _bottomnav(html)
    taste_block = bottomnav[bottomnav.index('href="/profile"'):bottomnav.index("</a>", bottomnav.index('href="/profile"'))]
    assert "M4 13v-2M8 15.5v-7M12 17v-10M16 15.5v-7M20 13v-2" in taste_block


def test_taste_still_unrelated_to_theme_iconography():
    html = _read("templates/base.html")
    bottomnav = _bottomnav(html)
    taste_block = bottomnav[bottomnav.index('href="/profile"'):bottomnav.index("</a>", bottomnav.index('href="/profile"'))]
    assert "wv-theme-icon" not in taste_block
    assert 'r="4.2"' not in taste_block  # the sun's ray-circle radius
    assert "M20 14.5A8.5" not in taste_block  # the moon crescent


# ── 11/12/13. fifth control: Account, not a visible "More" ───────────

def test_authenticated_fifth_control_uses_avatar_or_monogram(monkeypatch):
    client = _dashboard_client(monkeypatch)
    _login(client, monkeypatch)
    html = client.get("/").data.decode()
    bottomnav = _bottomnav(html)
    account_block = bottomnav[bottomnav.index('id="wv-more-btn"'):bottomnav.index("</button>", bottomnav.index('id="wv-more-btn"'))]
    assert "wv-account-avatar-mono" in account_block or "wv-account-avatar-img" in account_block
    assert "<svg" not in account_block  # no generic person icon while authenticated


def test_authenticated_avatar_image_has_empty_alt(monkeypatch):
    client = _dashboard_client(monkeypatch)
    monkeypatch.setattr(User, "get", classmethod(
        lambda cls, i: User(id=1, username="dimos", email="d@e.com", password_hash="x",
                             profile_image_url="https://res.cloudinary.com/demo/dimos.jpg")
    ))
    with client.session_transaction() as sess:
        sess["_user_id"] = "1"
    html = client.get("/").data.decode()
    bottomnav = _bottomnav(html)
    account_block = bottomnav[bottomnav.index('id="wv-more-btn"'):bottomnav.index("</button>", bottomnav.index('id="wv-more-btn"'))]
    assert '<img class="wv-account-avatar-img" src="https://res.cloudinary.com/demo/dimos.jpg" alt="">' in account_block


def test_logged_out_fifth_control_uses_a_person_svg(monkeypatch):
    html = _dashboard_client(monkeypatch).get("/").data.decode()
    bottomnav = _bottomnav(html)
    account_block = bottomnav[bottomnav.index('id="wv-more-btn"'):bottomnav.index("</button>", bottomnav.index('id="wv-more-btn"'))]
    assert "<svg" in account_block
    assert "wv-account-avatar-img" not in account_block
    assert "wv-account-avatar-mono" not in account_block


def test_fifth_control_is_still_a_button_opening_the_existing_panel():
    html = _read("templates/base.html")
    bottomnav = _bottomnav(html)
    trigger_tag = bottomnav[bottomnav.index('<button type="button" class="wv-moretrigger"'):bottomnav.index(">", bottomnav.index('<button type="button" class="wv-moretrigger"')) + 1]
    assert 'aria-haspopup="dialog"' in trigger_tag
    assert 'aria-controls="wv-morepanel"' in trigger_tag


def test_fifth_control_accessible_label_says_account(monkeypatch):
    html = _dashboard_client(monkeypatch).get("/").data.decode()
    bottomnav = _bottomnav(html)
    assert 'aria-label="Account"' in bottomnav

    client = _dashboard_client(monkeypatch)
    _login(client, monkeypatch)
    html_in = client.get("/").data.decode()
    assert 'aria-label="Account menu, @dimos"' in _bottomnav(html_in)


def test_no_visible_more_text_remains_anywhere_in_bottom_bar():
    html = _read("templates/base.html")
    bottomnav = _bottomnav(html)
    assert ">More<" not in bottomnav
    assert "wv-sr-only\">More<" not in bottomnav


# ── active account-sheet ring, avatar undistorted, no looping motion ──

def test_account_icon_gets_a_ring_only_while_the_sheet_is_open():
    css = _read("static/css/waveline.css")
    rule = re.search(r'\.wv-moretrigger\[aria-expanded="true"\] \.wv-account-ic \{([^}]*)\}', css)
    assert rule, "expected a ring rule keyed off aria-expanded (already set by initMorePanel())"
    assert "box-shadow" in rule.group(1)
    assert "var(--wv-accent)" in rule.group(1)
    # the standalone (non-compound) .wv-account-ic rule must not itself
    # carry a box-shadow — it should only ever appear via the aria-expanded
    # compound selector matched above
    bare_rule = re.search(r"(?<!\] )\.wv-account-ic \{([^}]*)\}", css)
    assert bare_rule and "box-shadow" not in bare_rule.group(1)


def test_avatar_and_monogram_stay_perfectly_round_and_not_stretched():
    css = _read("static/css/waveline.css")
    rule = re.search(r"\.wv-account-avatar-img, \.wv-account-avatar-mono \{([^}]*)\}", css).group(1)
    assert "border-radius: 50%" in rule
    assert "object-fit: cover" in rule  # img never stretches/distorts
    assert re.search(r"width:\s*(\d+)px", rule).group(1) == re.search(r"height:\s*(\d+)px", rule).group(1)


# ── 10. bottom-bar style: no coral circle, capped one-shot animation ──

def test_no_coral_soft_circle_behind_active_icons():
    css = _read("static/css/waveline.css")
    mobile_block = re.search(r"@media \(max-width: 860px\) \{(.*?)\n\}\n", css, re.S).group(1)
    active_rule = re.search(r'\.wv-bottomnav a\[aria-current="page"\] \.ic \{([^}]*)\}', mobile_block).group(1)
    assert "background" not in active_rule


def test_active_animation_is_capped_short_subtle_and_one_shot():
    css = _read("static/css/waveline.css")
    mobile_block = re.search(r"@media \(max-width: 860px\) \{(.*?)\n\}\n", css, re.S).group(1)
    active_rule = re.search(r'\.wv-bottomnav a\[aria-current="page"\] \.ic \{([^}]*)\}', mobile_block).group(1)
    animation = re.search(r"animation:\s*([^;]+);", active_rule).group(1).split()
    name, duration, iteration = animation[0], animation[1], animation[-1]
    assert duration.endswith("s")
    seconds = float(duration.rstrip("s"))
    assert seconds <= 0.18
    assert iteration == "1"  # never "infinite"
    keyframes = re.search(r"@keyframes " + name + r" \{([^}]*(?:\{[^}]*\}[^}]*)*)\}", mobile_block, re.S).group(1)
    scales = [float(s) for s in re.findall(r"scale\(([\d.]+)\)", keyframes)]
    assert all(s <= 1.04 for s in scales)


def test_bottom_bar_reduced_motion_still_covered_globally():
    css = _read("static/css/waveline.css")
    block = re.search(r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n\}\n", css, re.S).group(1)
    assert "animation-duration: .001ms !important" in block


def test_touch_targets_in_the_48_to_56px_range():
    css = _read("static/css/waveline.css")
    mobile_block = re.search(r"@media \(max-width: 860px\) \{(.*?)\n\}\n", css, re.S).group(1)
    rule = re.search(r"\.wv-bottomnav a, \.wv-bottomnav button\.wv-moretrigger \{([^}]*)\}", mobile_block).group(1)
    min_height = int(re.search(r"min-height:\s*(\d+)px", rule).group(1))
    assert 48 <= min_height <= 56


def test_bottom_nav_height_not_dramatically_increased():
    css = _read("static/css/waveline.css")
    assert "--wv-bottomnav-h: 60px;" in css


# ── 14. badges: header unchanged, account gets a subtle dot only ────

def test_account_button_dot_reuses_the_existing_subtle_indicator(monkeypatch):
    import dashboard as dashboard_module
    monkeypatch.setattr(dashboard_module, "count_unread", lambda uid: 1)
    client = _dashboard_client(monkeypatch)
    _login(client, monkeypatch)
    html = client.get("/").data.decode()
    bottomnav = _bottomnav(html)
    account_block = bottomnav[bottomnav.index('id="wv-more-btn"'):bottomnav.index("</button>", bottomnav.index('id="wv-more-btn"'))]
    assert '<span class="wv-moredot" aria-hidden="true"></span>' in account_block
    # never a large number badge on the avatar itself
    assert "wv-badge" not in account_block


def test_mobile_header_badges_untouched():
    css = _read("static/css/waveline.css")
    assert ".wv-header .wv-iconbtn.wv-mobile-only { min-width: 44px; height: 44px; }" in css


# ── safe-area / desktop untouched / focus behaviour preserved ───────

def test_safe_area_logic_intact():
    css = _read("static/css/waveline.css")
    assert re.search(
        r"body \{ padding-bottom: calc\(var\(--wv-bottomnav-h\) \+ env\(safe-area-inset-bottom, 0\)\); \}", css
    )


def test_desktop_navigation_unaffected():
    html = _read("templates/base.html")
    assert '<nav class="wv-nav" aria-label="Primary">' in html
    for label in ["Home", "Discover", "Feed", "Taste"]:
        assert f">{label}<" in _section(html, '<nav class="wv-nav"', "</nav>")


def test_desktop_dropdowns_still_present():
    html = _read("templates/base.html")
    assert 'class="wv-msgmenu wv-desktop-only"' in html
    assert 'class="wv-notifmenu wv-desktop-only"' in html
    assert 'id="wv-msg-panel"' in html
    assert 'id="wv-notif-panel"' in html


def test_more_panel_focus_behaviour_preserved():
    js = _read("static/js/nav.js")
    body = js[js.index("function initMorePanel"):js.index("function initNavDropdown")]
    assert 'if (e.key === "Escape") { close(); return; }' in body
    assert "lastFocus = document.activeElement;" in body
    assert "lastFocus.focus();" in body
    assert 'document.body.style.overflow = "hidden";' in body
    assert 'document.body.style.overflow = "";' in body
    assert "scrim.addEventListener(\"click\", close);" in body


def test_no_duplicate_ids_between_header_and_bottom_nav():
    html = _read("templates/base.html")
    ids = re.findall(r'\bid="([^"]+)"', html)
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, f"duplicate ids: {dupes}"


# ── account sheet content hierarchy preserved from Phase 8.1 ─────────

def test_authenticated_sheet_still_has_sign_out_messages_notifications_profile_settings(monkeypatch):
    client = _dashboard_client(monkeypatch)
    _login(client, monkeypatch)
    panel = _more_panel(client.get("/").data.decode())
    assert panel.count("Sign out") == 1
    assert 'class="wv-more-quick" href="/messages"' in panel
    assert 'class="wv-more-quick" href="/notifications"' in panel
    assert '<a href="/me">Profile</a>' in panel
    assert '<a href="/settings">Settings</a>' in panel


def test_logged_out_sheet_still_has_create_account_and_login_before_explore(monkeypatch):
    panel = _more_panel(_dashboard_client(monkeypatch).get("/").data.decode())
    assert 'href="/register"' in panel
    assert 'href="/login"' in panel
    assert panel.index("Join Waveline") < panel.index(">Explore<")

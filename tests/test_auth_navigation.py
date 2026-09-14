"""Tests for the authentication-navigation bugfix: no duplicate Sign up in
the desktop dock, a discoverable Logout action (desktop profile menu +
mobile More panel), and correct auth-state handling in the command palette.

Both the desktop dock and the mobile "More" panel are rendered into the same
server response (CSS/media-queries decide which is visible at which
breakpoint), so "no duplicates" is checked per-surface, not as a raw
whole-document substring count.
"""

import dashboard
import views_main
from models import User

FAKE = User(id=1, username="dimos", email="d@e.com", password_hash="x")


def _login(client, monkeypatch, user=FAKE):
    monkeypatch.setattr(User, "get", classmethod(lambda cls, i: user))
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)


def _dashboard_client(monkeypatch):
    monkeypatch.setattr(views_main, "get_dashboard_data", lambda: (
        10, 5, 2, [("Radiohead", 500, 500)], [], [],
    ))
    monkeypatch.setattr(views_main, "get_feed", lambda *a, **k: [])
    return dashboard.app.test_client()


def _section(html, start, end):
    i = html.index(start)
    j = html.index(end, i)
    return html[i:j]


def _dock(html):
    return _section(html, '<header class="wv-header"', "</header>")


def _more_panel(html):
    return _section(html, 'id="wv-morepanel"', '<!-- ═══ COMMAND PALETTE')


# ── 1/2/3: logged-out navigation ──

def test_desktop_dock_logged_out_has_exactly_one_login_and_one_signup(monkeypatch):
    # Phase 8.1: the mobile header gained its own compact, decoupled Log in
    # link (previously Log in was desktop-only with no mobile counterpart
    # at all) — so the header now legitimately contains two <a href="/login">
    # elements, one per surface, matching the wv-desktop-only/wv-mobile-only
    # pairing already used everywhere else (e.g. the search trigger). "Exactly
    # one" is checked per surface now, not as a single whole-header count —
    # same philosophy test_logged_out_dock_has_no_stray_third_signup_copy
    # already documents below. Sign up stays desktop-only only (it's
    # promoted prominently inside the mobile More panel instead).
    html = _dashboard_client(monkeypatch).get("/").data.decode()
    dock = _dock(html)
    assert dock.count('href="/login"') == 2
    assert dock.count('wv-desktop-only" href="/login"') == 1
    assert dock.count('wv-mobile-only" href="/login"') == 1
    assert dock.count('href="/register"') == 1
    assert "logout" not in dock.lower()


def test_mobile_more_panel_logged_out_has_exactly_one_login_and_one_signup(monkeypatch):
    html = _dashboard_client(monkeypatch).get("/").data.decode()
    panel = _more_panel(html)
    assert panel.count('href="/login"') == 1
    assert panel.count('href="/register"') == 1


def test_logged_out_dock_has_no_stray_third_signup_copy(monkeypatch):
    # Regression for the exact bug: a stray mobile-only block that made a
    # SECOND Sign up appear inside the desktop dock itself, alongside the
    # legitimate one. (Page *content* — e.g. the homepage's own "Create
    # account" CTA — legitimately links to /register too; this only checks
    # the navigation chrome.)
    #
    # Phase 7.5 introduces a real, correctly-scoped .wv-mobile-only utility
    # (hidden on desktop, shown only at/under the mobile breakpoint — see
    # the search-trigger duplicate-control fix), so the class name itself
    # is no longer a safe proxy for "the old bug is back." Assert the
    # actual invariant directly instead: exactly one Sign up in each surface.
    html = _dashboard_client(monkeypatch).get("/").data.decode()
    assert _dock(html).count('href="/register"') == 1
    assert _more_panel(html).count('href="/register"') == 1


# ── 4/5: logged-in navigation ──

def test_desktop_dock_logged_in_has_no_login_or_signup(monkeypatch):
    client = _dashboard_client(monkeypatch)
    _login(client, monkeypatch)
    dock = _dock(client.get("/").data.decode())
    assert 'href="/login"' not in dock
    assert 'href="/register"' not in dock


def test_desktop_dock_logged_in_has_logout_via_profile_menu(monkeypatch):
    client = _dashboard_client(monkeypatch)
    _login(client, monkeypatch)
    dock = _dock(client.get("/").data.decode())
    assert "Sign out" in dock
    assert 'id="wv-profile-trigger"' in dock
    assert 'aria-haspopup="menu"' in dock
    assert 'aria-expanded="false"' in dock
    assert 'aria-controls="wv-profile-menu"' in dock
    assert 'role="menu"' in dock
    # the old bug: username used to be a plain link, not a menu trigger
    assert '<a class="wv-userchip wv-desktop-only" href="/me">' not in dock


def test_avatar_menu_contains_all_six_account_destinations(monkeypatch):
    client = _dashboard_client(monkeypatch)
    _login(client, monkeypatch)
    html = client.get("/").data.decode()
    panel = _section(html, 'id="wv-profile-menu"', "</div>")

    expected = [
        ("/me", "Profile"),
        ("/messages", "Messages"),
        ("/profile", "Taste Profile"),
        ("/feed", "Feed"),
        ("/settings", "Settings"),
        ("/logout", "Sign out"),
    ]
    for href, label in expected:
        assert f'href="{href}"' in panel, f"missing link to {href}"
        assert f">{label}<" in panel, f"missing label {label!r}"

    # exactly these six menu items, no more, no fewer
    assert panel.count("role=\"menuitem\"") == 6
    # superseded labels from before this correction must be gone
    assert "View profile" not in panel
    assert ">Log out<" not in panel


def test_mobile_more_panel_logged_in_has_logout_and_no_login_signup(monkeypatch):
    # Phase 8.1: relabelled "Log out" -> "Sign out" in the mobile More panel
    # to match the desktop profile menu, which already deliberately uses
    # "Sign out" (see test_avatar_menu_contains_all_six_account_destinations
    # below, which asserts the old "Log out" label is gone from THAT panel).
    client = _dashboard_client(monkeypatch)
    _login(client, monkeypatch)
    panel = _more_panel(client.get("/").data.decode())
    assert panel.count("Sign out") == 1
    assert 'href="/login"' not in panel
    assert 'href="/register"' not in panel


# ── 6/7: logout behaviour hits the real Flask route and clears the session ──

def test_logout_uses_real_route_and_redirects(monkeypatch):
    client = _dashboard_client(monkeypatch)
    _login(client, monkeypatch)
    resp = client.get("/logout")
    assert resp.status_code == 302


def test_logout_clears_authenticated_session(monkeypatch):
    client = _dashboard_client(monkeypatch)
    _login(client, monkeypatch)
    client.get("/logout")
    # A login-required route must now redirect to /login — proof the session
    # was actually cleared, not just that /logout returned a redirect.
    resp = client.get("/notifications")
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("Location", "")


def test_base_template_uses_url_for_for_logout():
    with open("templates/base.html") as f:
        html = f.read()
    assert "url_for('auth.logout')" in html
    assert 'href="/logout"' not in html  # no hardcoded logout path


# ── 8: mobile bottom navigation has no auth actions to duplicate ──

def test_bottom_nav_has_no_auth_links(monkeypatch):
    html = _dashboard_client(monkeypatch).get("/").data.decode()
    bottomnav = _section(html, 'class="wv-bottomnav"', "</nav>")
    assert "/login" not in bottomnav
    assert "/register" not in bottomnav
    assert "/logout" not in bottomnav


# ── 9: command palette reflects the correct authenticated state ──

def test_window_wv_reflects_logged_out_state(monkeypatch):
    html = _dashboard_client(monkeypatch).get("/").data.decode()
    assert "authenticated: false" in html


def test_window_wv_reflects_logged_in_state(monkeypatch):
    client = _dashboard_client(monkeypatch)
    _login(client, monkeypatch)
    html = client.get("/").data.decode()
    assert "authenticated: true" in html
    assert 'username: "dimos"' in html


def test_command_palette_js_splits_logged_out_items():
    with open("static/js/command-palette.js") as f:
        js = f.read()
    assert '"Log in"' in js
    assert '"Create account"' in js


def test_command_palette_js_has_logged_in_items_including_logout():
    with open("static/js/command-palette.js") as f:
        js = f.read()
    assert '"View profile"' in js
    assert '"Notifications"' in js
    assert '"Log out"' in js


def test_command_palette_never_concats_both_states_at_once():
    with open("static/js/command-palette.js") as f:
        js = f.read()
    # authItems() must return one branch or the other, not both merged.
    assert "function authItems" in js
    assert js.count("function authItem(") == 0  # old singular/combined function is gone

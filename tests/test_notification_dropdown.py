"""Tests for Phase 4: the desktop notification-bell dropdown.

Covers: dropdown markup/accessibility wiring in base.html, the
POST /notifications/read mutation+preview endpoint (auth, scoping,
idempotency, never trusting a client-supplied user id), the actor-avatar
extension to social.get_notifications(), and that the full /notifications
page and the shared nav-dropdown JS wiring are untouched/consistent.
No real network or database calls anywhere in this file.
"""

import contextlib
import datetime

import dashboard
import social
import views_notifications
from models import User

OWNER = User(id=1, username="dimos", email="d@e.com", password_hash="x")
OTHER = User(id=2, username="alice", email="a@e.com", password_hash="x")


def _login(client, monkeypatch, user=OWNER):
    monkeypatch.setattr(User, "get", classmethod(lambda cls, i: user))
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)


def _sample_items():
    return [
        {"id": 1, "type": "like", "post_id": 9, "is_read": False,
         "created_at": datetime.datetime.utcnow(), "actor": "alice",
         "post_body": "loving this track", "actor_avatar": None},
        {"id": 2, "type": "follow", "post_id": None, "is_read": True,
         "created_at": datetime.datetime.utcnow(), "actor": "bob",
         "post_body": None, "actor_avatar": "https://res.cloudinary.com/demo/bob.jpg"},
    ]


# ── markup / accessibility ──────────────────────────────────────────

def test_bell_is_accessible_dropdown_trigger(monkeypatch):
    monkeypatch.setattr(dashboard, "count_unread", lambda uid: 0)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/about").data.decode()
    assert 'id="wv-notif-trigger"' in html
    trigger_region = html[html.index('id="wv-notif-trigger"'):html.index('id="wv-notif-trigger"') + 400]
    # A notification feed isn't an application menu (no arrow-key navigation
    # is implemented) — a disclosure dialog is the correct, honest semantic.
    assert 'aria-haspopup="dialog"' in trigger_region
    assert 'aria-controls="wv-notif-panel"' in html
    assert 'aria-expanded="false"' in trigger_region


def test_notif_panel_present_and_hidden_by_default(monkeypatch):
    monkeypatch.setattr(dashboard, "count_unread", lambda uid: 0)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/about").data.decode()
    assert 'id="wv-notif-panel"' in html
    assert 'role="dialog"' in html[html.index('id="wv-notif-panel"'):html.index('id="wv-notif-panel"') + 60]
    assert 'role="menu"' not in html[html.index('id="wv-notif-panel"') - 5:html.index('id="wv-notif-panel"') + 60]
    panel_tag = html[html.index('id="wv-notif-panel"') - 200:html.index('id="wv-notif-panel"') + 200]
    assert "hidden" in panel_tag


def test_notif_rows_and_footer_are_plain_links_not_menuitems(monkeypatch):
    monkeypatch.setattr(views_notifications, "get_notifications", lambda uid, limit=30: _sample_items())
    monkeypatch.setattr(views_notifications, "mark_all_read", lambda uid: None)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.post("/notifications/read").data.decode()
    assert "menuitem" not in html
    footer_html = client.get("/about").data.decode()
    assert 'class="wv-notifmenu-footer"' in footer_html
    footer_tag = footer_html[footer_html.index('class="wv-notifmenu-footer"') - 40:footer_html.index('class="wv-notifmenu-footer"') + 40]
    assert "menuitem" not in footer_tag


def test_profile_and_more_menu_semantics_untouched(monkeypatch):
    # This phase must not broadly refactor accessibility — only the
    # notification popup's role changes.
    monkeypatch.setattr(dashboard, "count_unread", lambda uid: 0)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/about").data.decode()
    assert 'aria-haspopup="menu"' in html[html.index('id="wv-profile-trigger"'):html.index('id="wv-profile-trigger"') + 200]
    assert 'role="menu"' in html[html.index('id="wv-profile-menu"'):html.index('id="wv-profile-menu"') + 60]
    assert 'aria-haspopup="menu"' in html[html.index('id="wv-navmenu-trigger"'):html.index('id="wv-navmenu-trigger"') + 300]
    assert 'role="menu"' in html[html.index('id="wv-navmenu-panel"'):html.index('id="wv-navmenu-panel"') + 60]


def test_footer_link_points_to_full_notifications_page(monkeypatch):
    monkeypatch.setattr(dashboard, "count_unread", lambda uid: 0)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/about").data.decode()
    assert 'class="wv-notifmenu-footer" href="/notifications"' in html
    assert "View all notifications" in html


def test_navbar_badge_still_renders_unchanged(monkeypatch):
    # Regression: adding the dropdown must not touch the exact badge markup
    # other tests (test_polish.py) assert on byte-for-byte.
    monkeypatch.setattr(dashboard, "count_unread", lambda uid: 4)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/about").data.decode()
    assert 'class="wv-badge">4' in html


def test_mobile_more_panel_badge_has_a_targeted_id(monkeypatch):
    # The mobile "More" panel keeps its own /notifications link+badge — JS
    # must be able to clear this one specifically (not every .wv-badge on
    # the page) once the desktop dropdown's fetch marks everything read.
    # Phase 8.1 added a third badge: the mobile HEADER's own Notifications
    # icon-link (a plain nav link, no dropdown/JS of its own — see
    # test_mobile_navigation_hotfix.py), so the total is now 3, not 2.
    monkeypatch.setattr(dashboard, "count_unread", lambda uid: 3)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/about").data.decode()
    assert 'id="wv-notif-badge-mobile"' in html
    assert html.count('class="wv-badge"') == 3  # desktop bell + mobile header icon + mobile More card
    assert 'href="/notifications"' in html
    assert ">Notifications<" in html


def test_logged_out_pages_have_no_notif_dropdown():
    client = dashboard.app.test_client()
    html = client.get("/about").data.decode()
    assert "wv-notif-trigger" not in html
    assert "wv-notif-panel" not in html


def test_nav_js_wires_notif_dropdown_through_shared_mechanism():
    # Guards the "only one nav dropdown open at once" requirement: the
    # notif dropdown must be registered through the same initNavDropdown()
    # factory (with its shared openNavDropdownClose) as the other two.
    with open("static/js/nav.js") as f:
        src = f.read()
    assert 'initNavDropdown("wv-notif-trigger", "wv-notif-panel"' in src
    assert "openNavDropdownClose" in src


def test_nav_js_handles_fetch_failure_without_getting_stuck():
    # Regression: a failed POST /notifications/read must not leave the
    # dropdown showing "Loading…" forever, and must not leave the catch
    # block empty (an empty catch was the previous, buggy behaviour).
    with open("static/js/nav.js") as f:
        src = f.read()
    notif_block = src[src.index("function initNotifDropdown"):src.index("/* ── Scroll-reveal")]
    assert ".catch(function ()" in notif_block
    catch_body = notif_block.split(".catch(function ()", 1)[1].split(".then(function () { inFlight = false; })")[0]
    assert catch_body.strip() != "{ }"
    assert "Couldn" in catch_body and "notifications" in catch_body.lower()
    # Failure must not touch the badge — only the success branch may remove it.
    assert "badge.remove()" not in catch_body


def test_nav_js_clears_both_desktop_and_mobile_badges_on_success():
    with open("static/js/nav.js") as f:
        src = f.read()
    notif_block = src[src.index("function initNotifDropdown"):src.index("/* ── Scroll-reveal")]
    success_body = notif_block.split(".then(function (html)", 1)[1].split(".catch(function ()")[0]
    assert 'trigger.querySelector(".wv-badge")' in success_body
    assert 'getElementById("wv-notif-badge-mobile")' in success_body
    assert 'setAttribute("aria-label", "Notifications")' in success_body


def test_nav_js_does_not_repeat_the_read_request_after_success():
    # Regression: initNotifDropdown() previously reset only `inFlight` after
    # each request, so every reopen re-POSTed /notifications/read even after
    # a prior success had already acknowledged everything. A `loaded` flag
    # (or equivalent) must short-circuit onNotifOpen() once acknowledgement
    # has actually succeeded, while a failed attempt must NOT set it, so the
    # next open still retries.
    with open("static/js/nav.js") as f:
        src = f.read()
    notif_block = src[src.index("function initNotifDropdown"):src.index("/* ── Scroll-reveal")]

    assert "var loaded = false;" in notif_block or "loaded = false" in notif_block

    guard_line = notif_block.split("return function onNotifOpen()", 1)[1].split("\n")[1]
    assert "loaded" in guard_line and "inFlight" in guard_line and "return" in guard_line

    success_body = notif_block.split(".then(function (html)", 1)[1].split(".catch(function ()")[0]
    assert "loaded = true;" in success_body

    catch_body = notif_block.split(".catch(function ()", 1)[1].split(".then(function () { inFlight = false; })")[0]
    assert "loaded = true" not in catch_body


def test_no_new_inline_script_added_to_base_html():
    with open("templates/base.html") as f:
        src = f.read()
    # Only the pre-existing theme-bootstrap and window.WV inline scripts.
    assert src.count("<script>") == 2


# ── POST /notifications/read: auth + scoping ────────────────────────

def test_notifications_read_requires_login():
    client = dashboard.app.test_client()
    resp = client.post("/notifications/read")
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("Location", "")


def test_notifications_read_get_not_allowed(monkeypatch):
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.get("/notifications/read")
    assert resp.status_code == 405


def test_notifications_read_scoped_to_current_user_only(monkeypatch):
    seen = {}

    def fake_get_notifications(uid, limit=30):
        seen["get_uid"] = uid
        return []

    def fake_mark_all_read(uid):
        seen["mark_uid"] = uid

    monkeypatch.setattr(views_notifications, "get_notifications", fake_get_notifications)
    monkeypatch.setattr(views_notifications, "mark_all_read", fake_mark_all_read)
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    # Even if a client tried to smuggle a different user id in the body,
    # the route reads no such parameter — it must be ignored entirely.
    resp = client.post("/notifications/read", data={"user_id": "999"})
    assert resp.status_code == 200
    assert seen["get_uid"] == OWNER.id
    assert seen["mark_uid"] == OWNER.id


def test_notifications_read_fetches_before_marking_read(monkeypatch):
    # Same ordering as the full /notifications page: fetch (so unread state
    # is still visible in the response) THEN mark read.
    order = []
    monkeypatch.setattr(views_notifications, "get_notifications",
                         lambda uid, limit=30: order.append("get") or [])
    monkeypatch.setattr(views_notifications, "mark_all_read",
                         lambda uid: order.append("mark"))
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    client.post("/notifications/read")
    assert order == ["get", "mark"]


def test_notifications_read_is_idempotent(monkeypatch):
    monkeypatch.setattr(views_notifications, "get_notifications", lambda uid, limit=30: _sample_items())
    calls = {"n": 0}

    def fake_mark_all_read(uid):
        calls["n"] += 1

    monkeypatch.setattr(views_notifications, "mark_all_read", fake_mark_all_read)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    r1 = client.post("/notifications/read")
    r2 = client.post("/notifications/read")
    assert r1.status_code == 200 and r2.status_code == 200
    assert calls["n"] == 2  # called each time, no error — safe to repeat


def test_notifications_read_uses_a_small_limit(monkeypatch):
    seen = {}
    monkeypatch.setattr(views_notifications, "get_notifications",
                         lambda uid, limit=30: seen.setdefault("limit", limit) or [])
    monkeypatch.setattr(views_notifications, "mark_all_read", lambda uid: None)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    client.post("/notifications/read")
    assert seen["limit"] == 8  # not the full-page default of 30


# ── POST /notifications/read: rendered content ──────────────────────

def test_notifications_read_renders_actor_action_snippet_and_time(monkeypatch):
    monkeypatch.setattr(views_notifications, "get_notifications", lambda uid, limit=30: _sample_items())
    monkeypatch.setattr(views_notifications, "mark_all_read", lambda uid: None)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.post("/notifications/read").data.decode()
    assert "@alice" in html and "liked your post" in html
    assert "loving this track" in html
    assert "@bob" in html and "started following you" in html
    assert 'class="nt-time"' in html


def test_notifications_read_shows_unread_dot_only_for_unread(monkeypatch):
    monkeypatch.setattr(views_notifications, "get_notifications", lambda uid, limit=30: _sample_items())
    monkeypatch.setattr(views_notifications, "mark_all_read", lambda uid: None)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.post("/notifications/read").data.decode()
    assert html.count('class="nt-dot"') == 1  # only alice's item was unread
    assert html.count("is-unread") == 1


def test_notifications_read_renders_real_avatar_photo_when_present(monkeypatch):
    monkeypatch.setattr(views_notifications, "get_notifications", lambda uid, limit=30: _sample_items())
    monkeypatch.setattr(views_notifications, "mark_all_read", lambda uid: None)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.post("/notifications/read").data.decode()
    assert '<img class="wv-avatar wv-avatar-xs" src="https://res.cloudinary.com/demo/bob.jpg"' in html


def test_notifications_read_falls_back_to_monogram_without_photo(monkeypatch):
    monkeypatch.setattr(views_notifications, "get_notifications", lambda uid, limit=30: _sample_items())
    monkeypatch.setattr(views_notifications, "mark_all_read", lambda uid: None)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.post("/notifications/read").data.decode()
    assert 'class="wv-avatar wv-avatar-xs" style="background:' in html


def test_notifications_read_empty_state(monkeypatch):
    monkeypatch.setattr(views_notifications, "get_notifications", lambda uid, limit=30: [])
    monkeypatch.setattr(views_notifications, "mark_all_read", lambda uid: None)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.post("/notifications/read").data.decode()
    assert "No notifications yet." in html


# ── social.get_notifications(): actor_avatar extension ──────────────

def test_get_notifications_selects_and_maps_actor_avatar(monkeypatch):
    seen = {}

    class FakeCur:
        def execute(self, sql, params=None):
            seen["sql"] = sql
            seen["params"] = params

        def fetchall(self):
            return [(1, "like", 5, False, None, "alice", "nice track",
                      "https://res.cloudinary.com/demo/alice.jpg")]

    @contextlib.contextmanager
    def fake_cm(commit=False):
        yield FakeCur()

    monkeypatch.setattr(social, "db_cursor", fake_cm)
    items = social.get_notifications(7, limit=8)
    assert "profile_image_url" in seen["sql"]
    assert seen["params"] == (7, 8)
    assert items[0]["actor_avatar"] == "https://res.cloudinary.com/demo/alice.jpg"


def test_get_notifications_maps_missing_actor_avatar_to_none(monkeypatch):
    class FakeCur:
        def execute(self, sql, params=None):
            pass

        def fetchall(self):
            return [(1, "follow", None, True, None, "bob", None, None)]

    @contextlib.contextmanager
    def fake_cm(commit=False):
        yield FakeCur()

    monkeypatch.setattr(social, "db_cursor", fake_cm)
    items = social.get_notifications(7)
    assert items[0]["actor_avatar"] is None


# ── full /notifications page: unaffected ────────────────────────────

def test_full_notifications_page_still_has_no_avatar_markup_change(monkeypatch):
    monkeypatch.setattr(views_notifications, "get_notifications",
                         lambda uid, limit=30: [{"id": 1, "type": "follow", "post_id": None,
                                                  "is_read": False,
                                                  "created_at": datetime.datetime.utcnow(),
                                                  "actor": "alice"}])
    monkeypatch.setattr(views_notifications, "mark_all_read", lambda uid: None)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.get("/notifications")
    assert resp.status_code == 200
    assert b"@alice" in resp.data
    assert b"started following you" in resp.data

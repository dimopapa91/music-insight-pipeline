"""Tests for Phase 7.5: experience fixes.

Four independent areas, each covered in its own section below:
1. duplicate desktop search control
2. Taste Profile minimum-artist rule + safe diagnostics + user-scoped refresh
3. Messages dropdown (mirrors the Phase 4 Notifications dropdown)
4. homepage copy/voice

No network calls anywhere in this file. Anthropic is always mocked at the
`anthropic.Anthropic` level (the same object views_taste.py imports lazily
inside the route, so patching the module attribute affects it correctly).
"""

import contextlib
import logging
from types import SimpleNamespace

import dashboard
import views_taste
import views_messages
from models import User

OWNER = User(id=1, username="dimos", email="d@e.com", password_hash="x")


def _login(client, monkeypatch, user=OWNER):
    monkeypatch.setattr(User, "get", classmethod(lambda cls, i: user))
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)


# ── 1. duplicate desktop search control ──────────────────────────────

def test_desktop_search_trigger_is_desktop_only():
    with open("templates/base.html") as f:
        html = f.read()
    trigger = html[html.index('id="wv-search-trigger"') - 300:html.index('id="wv-search-trigger"') + 40]
    assert "wv-desktop-only" in trigger


def test_mobile_search_trigger_uses_mobile_only_utility():
    with open("templates/base.html") as f:
        html = f.read()
    trigger = html[html.index('id="wv-search-trigger-mobile"') - 100:html.index('id="wv-search-trigger-mobile"') + 40]
    assert "wv-mobile-only" in trigger
    assert "wv-desktop-only" not in trigger


def test_both_search_triggers_open_the_same_palette():
    with open("templates/base.html") as f:
        html = f.read()
    desktop = html[html.index('id="wv-search-trigger"'):html.index('id="wv-search-trigger"') + 200]
    mobile = html[html.index('id="wv-search-trigger-mobile"'):html.index('id="wv-search-trigger-mobile"') + 200]
    assert 'aria-controls="wv-palette-overlay"' in desktop
    assert 'aria-controls="wv-palette-overlay"' in mobile


def test_no_duplicate_search_trigger_ids():
    with open("templates/base.html") as f:
        html = f.read()
    assert html.count('id="wv-search-trigger"') == 1
    assert html.count('id="wv-search-trigger-mobile"') == 1


def test_mobile_only_utility_hides_on_desktop_and_shows_at_breakpoint():
    with open("static/css/waveline.css") as f:
        css = f.read()
    assert ".wv-header .wv-mobile-only { display: none; }" in css
    media_start = css.index("@media (max-width: 860px)")
    media_block = css[media_start:media_start + 800]
    assert ".wv-header .wv-mobile-only { display: inline-flex; }" in media_block


def test_command_palette_markup_unchanged():
    with open("templates/base.html") as f:
        html = f.read()
    assert 'id="wv-palette-overlay"' in html
    assert 'id="wv-palette-input"' in html
    assert 'id="wv-palette-list"' in html


# ── 2. Taste Profile: minimum-artist rule + diagnostics ──────────────

class _FakeMessages:
    def __init__(self, response_text=None, exception=None):
        self._response_text = response_text
        self._exception = exception

    def create(self, **kwargs):
        if self._exception:
            raise self._exception
        return SimpleNamespace(content=[SimpleNamespace(text=self._response_text)])


class _FakeAnthropicClient:
    def __init__(self, response_text=None, exception=None):
        self.messages = _FakeMessages(response_text, exception)


def _fake_db_cursor_with_rows(rows):
    class FakeCur:
        def execute(self, sql, params=None):
            pass

        def fetchall(self):
            return rows

    @contextlib.contextmanager
    def cm(commit=False):
        yield FakeCur()

    return cm


def _artist_rows(n):
    return [(f"Artist{i}", [{"name": f"Track{i}", "playcount": 10}]) for i in range(n)]


def _no_anthropic_call(monkeypatch):
    import anthropic
    def _boom(*a, **k):
        raise AssertionError("Anthropic must not be called")
    monkeypatch.setattr(anthropic, "Anthropic", _boom)


def _mock_anthropic(monkeypatch, response_text=None, exception=None):
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic",
                         lambda api_key=None: _FakeAnthropicClient(response_text, exception))


def test_taste_min_artists_constant_is_three():
    assert views_taste.MIN_TASTE_ARTISTS == 3


def test_zero_artists_makes_no_anthropic_call(monkeypatch):
    monkeypatch.setattr(views_taste, "_taste_cache", {})
    monkeypatch.setattr(views_taste, "db_cursor", _fake_db_cursor_with_rows([]))
    _no_anthropic_call(monkeypatch)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.get("/profile")
    assert resp.status_code == 200
    assert b"forming" in resp.data.lower() or b"Your wave is forming." in resp.data


def test_one_artist_makes_no_anthropic_call(monkeypatch):
    monkeypatch.setattr(views_taste, "_taste_cache", {})
    monkeypatch.setattr(views_taste, "db_cursor", _fake_db_cursor_with_rows(_artist_rows(1)))
    _no_anthropic_call(monkeypatch)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/profile").data.decode()
    assert "1 / 3 artists explored" in html
    assert "Your wave is forming." in html


def test_two_artists_makes_no_anthropic_call(monkeypatch):
    monkeypatch.setattr(views_taste, "_taste_cache", {})
    monkeypatch.setattr(views_taste, "db_cursor", _fake_db_cursor_with_rows(_artist_rows(2)))
    _no_anthropic_call(monkeypatch)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/profile").data.decode()
    assert "2 / 3 artists explored" in html
    assert "Explore one more artist" in html


def test_three_artists_attempts_generation(monkeypatch):
    monkeypatch.setattr(views_taste, "_taste_cache", {})
    monkeypatch.setattr(views_taste, "db_cursor", _fake_db_cursor_with_rows(_artist_rows(3)))
    _mock_anthropic(monkeypatch, response_text="A calm generated taste profile.")
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/profile").data.decode()
    assert "A calm generated taste profile." in html
    assert "forming" not in html.lower()


def test_six_artists_attempts_generation(monkeypatch):
    monkeypatch.setattr(views_taste, "_taste_cache", {})
    monkeypatch.setattr(views_taste, "db_cursor", _fake_db_cursor_with_rows(_artist_rows(6)))
    _mock_anthropic(monkeypatch, response_text="Six-artist taste profile text.")
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/profile").data.decode()
    assert "Six-artist taste profile text." in html


def test_successful_result_renders(monkeypatch):
    monkeypatch.setattr(views_taste, "_taste_cache", {})
    monkeypatch.setattr(views_taste, "db_cursor", _fake_db_cursor_with_rows(_artist_rows(3)))
    _mock_anthropic(monkeypatch, response_text="Your taste leans ambient and textured.")
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/profile").data.decode()
    assert "Your taste leans ambient and textured." in html
    assert "couldn&#39;t tune in" not in html and "couldn’t tune in" not in html


def test_taste_profile_strips_em_dashes_even_if_the_model_ignores_the_prompt(monkeypatch):
    # The prompt already asks Claude not to use em dashes, but that's a
    # request, not a guarantee -- this proves the deterministic fallback
    # (text_clean.strip_em_dashes) catches a slip before it's ever cached.
    monkeypatch.setattr(views_taste, "_taste_cache", {})
    monkeypatch.setattr(views_taste, "db_cursor", _fake_db_cursor_with_rows(_artist_rows(3)))
    _mock_anthropic(monkeypatch, response_text="Ambient textures — sparse rhythms — hazy vocals.")
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/profile").data.decode()
    # Narrow check: the page title/static chrome may legitimately contain its
    # own em dash ("Taste profile — Waveline"); what matters is the
    # AI-generated text itself is clean.
    assert "Ambient textures, sparse rhythms, hazy vocals." in html
    assert "Ambient textures — sparse" not in html
    # And the cleaned version, not the raw one, is what got cached.
    cache_key = f"{OWNER.id}:" + ",".join(sorted(f"Artist{i}" for i in range(3)))
    assert views_taste._taste_cache[cache_key] == "Ambient textures, sparse rhythms, hazy vocals."


def test_provider_failure_renders_graceful_fallback(monkeypatch):
    monkeypatch.setattr(views_taste, "_taste_cache", {})
    monkeypatch.setattr(views_taste, "db_cursor", _fake_db_cursor_with_rows(_artist_rows(3)))
    _mock_anthropic(monkeypatch, exception=RuntimeError("boom"))
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.get("/profile")
    html = resp.data.decode()
    assert resp.status_code == 200
    assert "couldn't tune in" in html or "couldn’t tune in" in html
    # calm notice, not a big alarming error box
    assert "wv-notice-error" not in html
    # artist chips still visible
    assert "Artist0" in html


def test_provider_failure_is_not_cached_as_success(monkeypatch):
    monkeypatch.setattr(views_taste, "_taste_cache", {})
    monkeypatch.setattr(views_taste, "db_cursor", _fake_db_cursor_with_rows(_artist_rows(3)))
    _mock_anthropic(monkeypatch, exception=RuntimeError("boom"))
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    client.get("/profile")
    cache_key = f"{OWNER.id}:" + ",".join(sorted(f"Artist{i}" for i in range(3)))
    assert cache_key not in views_taste._taste_cache


def test_taste_generation_failure_logs_safe_metadata_only(monkeypatch, caplog):
    monkeypatch.setattr(views_taste, "_taste_cache", {})
    monkeypatch.setattr(views_taste, "db_cursor", _fake_db_cursor_with_rows(_artist_rows(3)))
    _mock_anthropic(monkeypatch, exception=RuntimeError("super-secret-prompt-content"))
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    with caplog.at_level(logging.ERROR):
        resp = client.get("/profile")
    assert resp.status_code == 200
    joined = " ".join(r.message for r in caplog.records)
    assert "Taste generation failed" in joined
    assert "RuntimeError" in joined
    assert "super-secret-prompt-content" not in joined
    assert "Artist0" not in joined


def test_refresh_requires_login():
    client = dashboard.app.test_client()
    resp = client.post("/profile/refresh")
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("Location", "")


def test_refresh_clears_only_current_users_cache_entries(monkeypatch):
    other_key = "999:Other1,Other2,Other3"
    mine_key = f"{OWNER.id}:Artist0,Artist1,Artist2"
    monkeypatch.setattr(views_taste, "_taste_cache", {other_key: "other analysis", mine_key: "my analysis"})
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.post("/profile/refresh")
    assert resp.status_code == 302
    assert mine_key not in views_taste._taste_cache
    assert other_key in views_taste._taste_cache  # another user's cache untouched


def test_anonymous_profile_still_works(monkeypatch):
    client = dashboard.app.test_client()
    resp = client.get("/profile")
    assert resp.status_code == 200
    assert b"Create account" in resp.data


def test_no_provider_detail_leaks_into_html(monkeypatch):
    monkeypatch.setattr(views_taste, "_taste_cache", {})
    monkeypatch.setattr(views_taste, "db_cursor", _fake_db_cursor_with_rows(_artist_rows(3)))
    _mock_anthropic(monkeypatch, exception=RuntimeError("Authorization: Bearer sk-ant-secret"))
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/profile").data.decode()
    assert "sk-ant-secret" not in html
    assert "RuntimeError" not in html
    assert "Bearer" not in html


# ── 3. Messages dropdown ──────────────────────────────────────────────

def _conv(other_id=2, username="alice", profile_image_url="", last_body="hey",
          last_sender_id=2, unread_count=0):
    import datetime
    return {
        "conversation_id": 1, "other_id": other_id, "username": username,
        "profile_image_url": profile_image_url, "last_body": last_body,
        "last_created_at": datetime.datetime.utcnow(), "last_sender_id": last_sender_id,
        "unread_count": unread_count,
    }


def test_message_preview_requires_login():
    client = dashboard.app.test_client()
    resp = client.get("/messages/preview")
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("Location", "")


def test_message_preview_uses_small_inbox_limit(monkeypatch):
    seen = {}
    def fake_get_inbox(uid, limit=50):
        seen["limit"] = limit
        return []
    monkeypatch.setattr(views_messages, "get_inbox", fake_get_inbox)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    client.get("/messages/preview")
    assert seen["limit"] == 5
    assert seen["limit"] != 50  # not the full inbox limit


def test_message_preview_empty_state(monkeypatch):
    monkeypatch.setattr(views_messages, "get_inbox", lambda uid, limit=50: [])
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/messages/preview").data.decode()
    assert "No messages yet." in html


def test_message_preview_links_to_correct_thread(monkeypatch):
    monkeypatch.setattr(views_messages, "get_inbox", lambda uid, limit=50: [_conv(username="alice")])
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/messages/preview").data.decode()
    assert 'href="/messages/u/alice#messages-end"' in html


def test_message_preview_never_calls_mark_conversation_read(monkeypatch):
    monkeypatch.setattr(views_messages, "get_inbox", lambda uid, limit=50: [_conv(unread_count=3)])

    def _boom(*a, **k):
        raise AssertionError("preview must never mark anything read")
    monkeypatch.setattr(views_messages, "mark_conversation_read", _boom)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.get("/messages/preview")
    assert resp.status_code == 200


def test_message_preview_route_source_has_no_mutation():
    import inspect
    src = inspect.getsource(views_messages.preview)
    assert "mark_conversation_read" not in src
    assert "send_direct_message" not in src


def test_messages_dropdown_footer_links_to_inbox():
    with open("templates/base.html") as f:
        html = f.read()
    panel_start = html.index('id="wv-msg-panel"')
    panel_end = html.index("</div>\n                </div>", panel_start)
    panel = html[panel_start:panel_end]
    assert 'class="wv-msgmenu-footer" href="/messages"' in panel


def test_messages_envelope_is_a_disclosure_button_not_a_plain_link():
    with open("templates/base.html") as f:
        html = f.read()
    trigger_start = html.index('id="wv-msg-trigger"')
    trigger_tag = html[html.index("<button", 0, trigger_start):trigger_start + 200]
    assert "<button" in trigger_tag
    assert 'aria-haspopup="dialog"' in trigger_tag
    assert 'aria-controls="wv-msg-panel"' in trigger_tag
    assert 'aria-expanded="false"' in trigger_tag


def test_unread_message_badge_still_renders(monkeypatch):
    monkeypatch.setattr(dashboard, "count_unread_messages", lambda uid: 4)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/about").data.decode()
    dock = html[html.index('<header class="wv-header"'):html.index("</header>")]
    assert 'aria-label="Messages (4 unread)"' in dock
    assert 'class="wv-badge">4' in dock


def test_only_one_nav_dropdown_framework_is_used():
    with open("static/js/nav.js") as f:
        src = f.read()
    assert src.count("function initNavDropdown(") == 1
    assert 'initNavDropdown("wv-notif-trigger", "wv-notif-panel"' in src
    assert 'initNavDropdown("wv-msg-trigger", "wv-msg-panel"' in src
    # both share the exact same mutual-exclusion mechanism
    assert src.count("openNavDropdownClose") >= 2


def test_msg_dropdown_js_never_posts_or_marks_read():
    with open("static/js/nav.js") as f:
        src = f.read()
    msg_block = src[src.index("function initMsgDropdown"):src.index("function initReveal")]
    assert '"/messages/preview"' in msg_block
    assert "method: \"GET\"" in msg_block
    assert "POST" not in msg_block
    assert "badge.remove" not in msg_block  # unread state must not be faked away


# ── 4. Homepage voice ─────────────────────────────────────────────────

def _home_html(monkeypatch):
    import views_main
    monkeypatch.setattr(views_main, "get_dashboard_data", lambda: (0, 0, 0, [], [], []))
    monkeypatch.setattr(views_main, "get_feed", lambda *a, **k: [])
    client = dashboard.app.test_client()
    return client.get("/").data.decode()


def test_homepage_new_hero_heading(monkeypatch):
    html = _home_html(monkeypatch)
    assert "Follow the sound." in html


def test_homepage_new_hero_supporting_line(monkeypatch):
    html = _home_html(monkeypatch)
    assert "One artist can lead anywhere." in html


def test_homepage_new_search_placeholder(monkeypatch):
    html = _home_html(monkeypatch)
    assert 'placeholder="Start with an artist…"' in html


def test_homepage_tune_in_button(monkeypatch):
    html = _home_html(monkeypatch)
    hero_start = html.index('id="wv-title"')
    hero_end = html.index("</form>", hero_start)
    assert ">Tune in<" in html[hero_start:hero_end]


def test_homepage_old_tagline_absent(monkeypatch):
    html = _home_html(monkeypatch)
    assert "Find the signal behind the artist" not in html
    assert "Find the signal" not in html


def test_homepage_chapter_01_copy(monkeypatch):
    html = _home_html(monkeypatch)
    ch01 = html[html.index('id="ch01-h"'):html.index('id="ch01-h"') + 300]
    assert ">Tune in<" in ch01
    assert "Start with a name. See where it takes you." in ch01


def test_homepage_chapter_02_copy(monkeypatch):
    import datetime
    import views_main

    class _Insight:
        artist = "Radiohead"
        insight = "An insight about the band."
        searched_at = datetime.datetime(2026, 7, 5, 12, 0)
        top_tracks = ["Creep"]
        similar_artists = []

    monkeypatch.setattr(views_main, "get_dashboard_data", lambda: (0, 0, 0, [], [_Insight()], []))
    monkeypatch.setattr(views_main, "get_feed", lambda *a, **k: [])
    html = dashboard.app.test_client().get("/").data.decode()
    ch02 = html[html.index('id="ch02-h"'):html.index('id="ch02-h"') + 300]
    assert ">Go deeper<" in ch02
    assert "Tracks, context, and the shape behind the sound." in ch02


def test_homepage_chapter_03_copy(monkeypatch):
    import views_main
    monkeypatch.setattr(views_main, "get_dashboard_data", lambda: (
        0, 0, 0, [], [], [{"name": "Blur", "image": "", "nb_fan": 100}],
    ))
    monkeypatch.setattr(views_main, "get_feed", lambda *a, **k: [])
    html = dashboard.app.test_client().get("/").data.decode()
    ch03 = html[html.index('id="ch03-h"'):html.index('id="ch03-h"') + 300]
    assert ">Follow the current<" in ch03
    assert "Move sideways into something you might not have found." in ch03


def test_homepage_chapter_04_copy(monkeypatch):
    html = _home_html(monkeypatch)
    ch04 = html[html.index('id="ch04-h"'):html.index('id="ch04-h"') + 300]
    assert ">Find your people<" in ch04
    assert "Share what you find. Follow taste that takes you somewhere new." in ch04


def test_homepage_accessible_search_label_unchanged(monkeypatch):
    html = _home_html(monkeypatch)
    assert 'aria-label="Search for an artist"' in html


def test_homepage_title_and_meta_changed(monkeypatch):
    html = _home_html(monkeypatch)
    assert "<title>Waveline — Follow the sound</title>" in html
    assert 'content="Explore artists, discover connections and shape your music taste with Waveline.' in html


def test_footer_copy_updated():
    with open("templates/base.html") as f:
        html = f.read()
    assert "Music discovery, shaped by curiosity." in html
    assert "A living music-data signal" not in html
    # data-source attribution preserved
    assert "Last.fm, Spotify" in html
    assert "Anthropic Claude" in html


def test_secondary_chapter_search_form_removed_in_favour_of_quiet_link(monkeypatch):
    html = _home_html(monkeypatch)
    assert 'id="artist-input-2"' not in html
    ch01 = html[html.index('id="ch01-h"'):html.index('id="chapter-02"') if 'id="chapter-02"' in html else len(html)]
    assert 'href="#top"' in ch01


def test_search_route_behaviour_unregressed(monkeypatch):
    import views_main
    seen = {}
    monkeypatch.setattr(views_main, "run_pipeline", lambda artist, user_id=None: seen.update(artist=artist))
    client = dashboard.app.test_client()
    resp = client.post("/search", data={"artist": "Radiohead"})
    assert resp.status_code == 302
    assert seen["artist"] == "Radiohead"

"""Tests for Phase 8: responsive, accessibility & final polish.

Covers the concrete, confirmed fixes made during the Phase 8 audit — not a
re-test of every prior phase's behaviour (those suites already cover that).
Sections below correspond to the areas of the Phase 8 brief they verify.
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


# ── external links: rel="noopener noreferrer" ────────────────────────

def test_every_target_blank_link_has_noopener_noreferrer():
    import glob
    for path in glob.glob("templates/*.html"):
        html = _read(path)
        for m in re.finditer(r'<a\b[^>]*target="_blank"[^>]*>', html):
            tag = m.group(0)
            relm = re.search(r'rel="([^"]*)"', tag)
            assert relm, f"{path}: target=_blank link missing rel entirely: {tag}"
            rel_values = relm.group(1).split()
            assert "noopener" in rel_values, f"{path}: missing noopener: {tag}"
            assert "noreferrer" in rel_values, f"{path}: missing noreferrer: {tag}"


# ── stale copy: "Discover" used to mean "/" before Phase 5 added a real /discover ──

def test_error_page_links_back_to_home_not_stale_discover_copy():
    html = _read("templates/error.html")
    assert "← Back to Home" in html
    assert "Discover" not in html


def test_artist_page_back_link_says_home_not_stale_discover_copy():
    html = _read("templates/artist_profile.html")
    assert '<a class="ar-back" href="/">← Home</a>' in html


def test_artist_page_community_link_uses_canonical_latest_tab():
    html = _read("templates/artist_profile.html")
    assert 'href="/feed?tab=latest">Talk about it in Community</a>' in html
    assert "tab=discover" not in html


# ── 404 / 500 use the Waveline shell, never leak a stack trace ───────

def test_unknown_route_returns_waveline_shelled_404():
    client = dashboard.app.test_client()
    resp = client.get("/this-route-does-not-exist")
    assert resp.status_code == 404
    html = resp.data.decode()
    assert 'class="wv-header"' in html  # real shell, not Flask's default page
    assert "Page not found" in html
    assert "Traceback" not in html


def test_unhandled_exception_returns_waveline_shelled_500(monkeypatch):
    def _boom():
        raise RuntimeError("simulated failure")
    monkeypatch.setattr(views_main, "get_dashboard_data", _boom)
    client = dashboard.app.test_client()
    resp = client.get("/")
    assert resp.status_code == 500
    html = resp.data.decode()
    assert 'class="wv-header"' in html
    assert "Something went wrong" in html
    assert "Traceback" not in html
    assert "RuntimeError" not in html


# ── compare.html icon-only play buttons need an accessible name ─────

def test_compare_play_buttons_have_aria_label():
    html = _read("templates/compare.html")
    buttons = re.findall(r"<button type=\"button\"[^>]*wvPlay[^>]*>", html)
    assert len(buttons) == 2
    for b in buttons:
        assert 'aria-label="Play ' in b


def test_compare_track_buttons_meet_the_44px_touch_target():
    # Only 5 tracks render per artist (services.py), so the full 44px
    # target doesn't force an awkwardly tall card — no compact-layout
    # trade-off needed here after all.
    css = _read("templates/compare.html")
    rule = re.search(r"\.cp-track button \{[^}]*\}", css).group(0)
    assert "min-height: 44px" in rule
    assert "min-width: 44px" in rule


# ── command-palette input keyboard focus must stay visible ──────────

def test_palette_input_focus_visible_has_a_real_indicator():
    css = _read("static/css/waveline.css")
    # the old bug: `.wv-palette-inputrow input:focus { outline: none; }` with
    # no replacement at all, silently overriding the global :focus-visible rule
    assert re.search(r"\.wv-palette-inputrow input:focus\s*\{\s*outline:\s*none;\s*\}", css) is None
    rule = re.search(r"\.wv-palette-inputrow input:focus-visible \{([^}]*)\}", css)
    assert rule, "expected a :focus-visible rule for the palette input"
    assert "box-shadow" in rule.group(1)


def _fn_body(js, name):
    """Extract a top-level `function name(...) { ... }` body by brace-matching
    (regex alone can't handle nested braces reliably). Structural-source
    inspection only — there's no DOM/browser available in this suite (see
    module docstring), so this proves the code's *shape*, not runtime
    behaviour in a real browser."""
    m = re.search(r"function " + name + r"\s*\([^)]*\)\s*\{", js)
    assert m, f"function {name} not found"
    start = m.end()
    depth = 1
    i = start
    while depth:
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
        i += 1
    return js[start:i - 1]


# The command palette has no real DOM/browser test harness available (no
# jsdom/browser-test dependency exists in this repo, and Phase 8 explicitly
# says not to add one solely for this phase). These tests instead prove the
# *source shape* of the aria-activedescendant lifecycle: unique ids, that
# every render() exit path reconciles selection state, and that Arrow
# key/close handling touch the right functions. This is a real but partial
# substitute for genuine keyboard/AT testing in a browser.

def test_palette_items_get_a_stable_unique_id_never_reused():
    js = _read("static/js/command-palette.js")
    add_item = _fn_body(js, "addItem")
    assert 'el.id = "wv-palette-item-" + (itemSeq++)' in add_item
    # itemSeq must be declared exactly once, module-level, monotonic (never
    # reset) — reusing/resetting it across renders risks two live elements
    # sharing an id right after a re-render.
    assert js.count("var itemSeq") == 1
    assert "itemSeq = 0" in js
    assert "itemSeq--" not in js and "itemSeq = 0;\n" not in add_item


def test_update_selection_sets_or_clears_aria_activedescendant_from_live_dom():
    js = _read("static/js/command-palette.js")
    body = _fn_body(js, "updateSelection")
    # must re-query the live list (`items()`) rather than reuse a cached
    # array from a previous render, or it could reference a removed node
    assert "items()" in body
    assert 'input.setAttribute("aria-activedescendant"' in body
    assert 'input.removeAttribute("aria-activedescendant")' in body
    # the id given to the input must come from the actual DOM element, not
    # a computed/guessed string
    assert "els[activeIndex].id" in body


def test_render_resets_active_index_on_every_call():
    js = _read("static/js/command-palette.js")
    body = _fn_body(js, "render")
    assert re.search(r"list\.innerHTML\s*=\s*\"\";\s*\n\s*activeIndex\s*=\s*-1;", body), \
        "activeIndex must reset before the old list is even queried, on every render()"


def test_render_reconciles_selection_on_the_no_matches_early_return_path():
    # Regression: render() used to `return` from the empty-results branch
    # without calling updateSelection(), so a stale aria-activedescendant
    # from a previous render could keep pointing at an element that
    # `list.innerHTML = ""` had already destroyed.
    js = _read("static/js/command-palette.js")
    branch_start = js.index("if (!destMatches.length")
    branch_return = js.index("return;", branch_start)
    branch = js[branch_start:branch_return]
    assert "updateSelection();" in branch


def test_arrow_keys_move_active_index_and_reconcile_selection():
    js = _read("static/js/command-palette.js")
    # both arrow handlers must call updateSelection() so
    # aria-activedescendant tracks the move, and must clamp within bounds
    assert re.search(r'ArrowDown.*?activeIndex = Math\.min\(activeIndex \+ 1, els\.length - 1\);\s*updateSelection\(\);', js)
    assert re.search(r'ArrowUp.*?activeIndex = Math\.max\(activeIndex - 1, 0\);\s*updateSelection\(\);', js)


def test_close_clears_active_descendant():
    js = _read("static/js/command-palette.js")
    body = _fn_body(js, "close")
    assert 'input.removeAttribute("aria-activedescendant")' in body


def test_open_always_rerenders_into_a_valid_selection_state():
    js = _read("static/js/command-palette.js")
    body = _fn_body(js, "open")
    assert 'render("")' in body


# ── skip link target uses the requested #main-content convention ────

def test_skip_link_targets_main_content_id():
    html = _read("templates/base.html")
    assert '<a class="wv-skip" href="#main-content">' in html
    assert '<main id="main-content">' in html
    assert 'id="main"' not in html.replace('id="main-content"', "")


# ── messages thread page needs a real heading ────────────────────────

def test_messages_thread_page_has_exactly_one_h1():
    html = _read("templates/messages_thread.html")
    assert len(re.findall(r"<h1\b", html)) == 1
    assert '<h1 class="dm-thread-name">' in html


# ── user-generated / variable-length text must not force page overflow ──

def test_discover_card_text_wraps_safely():
    html = _read("templates/discover.html")
    assert re.search(r"\.dc-name \{[^}]*overflow-wrap:\s*anywhere", html)
    assert re.search(r"\.dc-bio \{[^}]*overflow-wrap:\s*anywhere", html)


def test_comment_text_wraps_safely():
    css = _read("static/css/waveline.css")
    assert re.search(r"\.wv-comment \{[^}]*overflow-wrap:\s*anywhere", css)


def test_profile_name_and_meta_wrap_safely():
    # .pf-name is a flex item in a row layout (.pf-identity-top) above the
    # 560px breakpoint, so it also needs min-width:0 (not just wrapping) or
    # a long unbroken username can force the row wider than the viewport.
    html = _read("templates/profile.html")
    assert re.search(r"\.pf-name \{[^}]*min-width:\s*0[^}]*overflow-wrap:\s*anywhere", html) or \
        re.search(r"\.pf-name \{[^}]*overflow-wrap:\s*anywhere[^}]*min-width:\s*0", html)
    assert re.search(r"\.pf-meta \{[^}]*overflow-wrap:\s*anywhere", html)


def test_notification_snippet_wraps_safely():
    css = _read("static/css/waveline.css")
    assert re.search(r"\.nt-snippet \{[^}]*overflow-wrap:\s*anywhere", css)


def test_chip_component_never_forces_page_overflow_from_long_user_text():
    # Genres are free-typed (settings.html, maxlength 255) and rendered as
    # .wv-chip pills; a single 255-char token with no commas would otherwise
    # render as one unbreakable pill wider than the viewport.
    css = _read("static/css/waveline.css")
    rule = re.search(r"\.wv-chip \{([^}]*)\}", css).group(1)
    assert "max-width: 100%" in rule
    assert "overflow-wrap: anywhere" in rule


def test_post_artist_tag_and_authors_wrap_safely():
    # post.artist is a free-typed tag (feed.html #cm-artist, maxlength 255)
    # rendered as a pill in the post header row.
    css = _read("static/css/waveline.css")
    assert re.search(r"\.wv-post-artist \{[^}]*overflow-wrap:\s*anywhere", css)
    assert re.search(r"\.wv-post-artist \{[^}]*max-width:\s*100%", css)
    for selector in [".wv-post-author", ".wv-c-author", ".dm-name", ".nt-actor"]:
        pattern = re.escape(selector) + r" \{[^}]*overflow-wrap:\s*anywhere"
        assert re.search(pattern, css), f"{selector} should wrap a long unbroken username safely"


# ── header crowding (861-1100px): trim width, keep every destination ──

def test_header_crowding_utility_hides_only_secondary_text_not_destinations(monkeypatch):
    monkeypatch.setattr(views_main, "get_dashboard_data", lambda: (10, 5, 2, [], [], []))
    monkeypatch.setattr(views_main, "get_feed", lambda *a, **k: [])
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/").data.decode()
    dock = html[html.index('<header class="wv-header"'):html.index("</header>")]
    # every real destination link/control must still be present in markup
    for href in ["/", "/discover", "/feed", "/profile", "/messages", "/notifications", "/settings"]:
        assert f'href="{href}"' in dock or f'aria-controls="wv-{href.strip("/")}' in dock
    assert 'class="wv-hide-crowded"' in dock


def test_profile_menu_trigger_has_accessible_name_independent_of_visible_text(monkeypatch):
    monkeypatch.setattr(views_main, "get_dashboard_data", lambda: (10, 5, 2, [], [], []))
    monkeypatch.setattr(views_main, "get_feed", lambda *a, **k: [])
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/").data.decode()
    assert 'aria-label="Account menu, @dimos"' in html


# ── mobile bottom-nav clearance (structural proof, section F) ───────

def test_body_bottom_padding_clears_the_fixed_bottom_nav_plus_safe_area():
    css = _read("static/css/waveline.css")
    mobile_block = re.search(r"@media \(max-width: 860px\) \{(.*?)\n\}\n", css, re.S)
    assert mobile_block, "expected the <=860px mobile media block"
    block = mobile_block.group(1)
    assert "height: var(--wv-bottomnav-h);" in block  # the nav's own height
    # body padding must reference the SAME token (not a duplicated magic
    # number that could silently drift from the nav's real height) plus the
    # safe-area inset, so fixed-bottom-nav content can never sit behind it
    assert re.search(
        r"body \{ padding-bottom: calc\(var\(--wv-bottomnav-h\) \+ env\(safe-area-inset-bottom, 0\)\); \}",
        block,
    )


def test_more_panel_sheet_respects_viewport_height_and_scrolls_internally():
    css = _read("static/css/waveline.css")
    rule = re.search(r"\.wv-morepanel-sheet \{([^}]*)\}", css).group(1)
    assert "max-height: 80vh" in rule
    assert "overflow-y: auto" in rule
    assert "env(safe-area-inset-bottom" in rule


def test_body_scroll_lock_restores_after_more_panel_closes():
    js = _read("static/js/nav.js")
    body = _fn_body(js, "initMorePanel")
    assert 'document.body.style.overflow = "hidden";' in body
    assert 'document.body.style.overflow = "";' in body


# ── 5. Mobile More keyboard logic — structural source inspection only.
#      There is no jsdom/Playwright in this repo (and Phase 8 says not to
#      add one solely for this phase), so these tests prove the *shape* of
#      initMorePanel()'s keyboard handling — that the right event listeners
#      and calls exist — not that a real browser executes them correctly. ──

def test_more_panel_closes_on_escape():
    js = _read("static/js/nav.js")
    body = _fn_body(js, "initMorePanel")
    # both inside the panel itself and globally (so Escape works even if
    # focus is somewhere unexpected while the panel is open)
    assert 'if (e.key === "Escape") { close(); return; }' in body
    assert 'if (e.key === "Escape" && !panel.hidden) close();' in body


def test_more_panel_traps_tab_focus():
    js = _read("static/js/nav.js")
    body = _fn_body(js, "initMorePanel")
    assert 'if (e.key === "Tab") {' in body
    assert "e.shiftKey && document.activeElement === first" in body
    assert "!e.shiftKey && document.activeElement === last" in body
    assert "e.preventDefault();" in body


def test_more_panel_returns_focus_to_the_trigger_on_close():
    js = _read("static/js/nav.js")
    body = _fn_body(js, "initMorePanel")
    # lastFocus is captured as document.activeElement at open() time, which
    # is the trigger button in the real click-to-open flow, then restored
    # in close()
    open_fn = body[body.index("function open()"):body.index("function close()")]
    close_fn = body[body.index("function close()"):]
    assert "lastFocus = document.activeElement;" in open_fn
    assert "lastFocus.focus();" in close_fn


def test_more_panel_locks_body_scroll_on_open():
    js = _read("static/js/nav.js")
    body = _fn_body(js, "initMorePanel")
    open_fn = body[body.index("function open()"):body.index("function close()")]
    assert 'document.body.style.overflow = "hidden";' in open_fn


def test_header_crowding_media_query_exists_and_does_not_hide_desktop_only():
    css = _read("static/css/waveline.css")
    m = re.search(
        r"@media \(min-width: 861px\) and \(max-width: 1100px\) \{([^}]*)\}", css
    )
    assert m, "expected an 861-1100px header-crowding media query"
    assert ".wv-hide-crowded" in m.group(1)
    assert "wv-desktop-only" not in m.group(1)  # must not remove a destination


# ── images/loading (section L) ───────────────────────────────────────

def test_profile_cover_hero_is_not_lazy_loaded():
    # Regression: the cover photo is the first thing rendered on the page
    # (above the fold), but had loading="lazy" — backwards, can cause a
    # visible pop-in for hero imagery.
    html = _read("templates/profile.html")
    hero_line = next(l for l in html.splitlines() if "pf-hero-img" in l)
    assert 'loading="lazy"' not in hero_line


def test_below_the_fold_discovery_images_stay_lazy():
    assert 'loading="lazy"' in _read("templates/index.html")
    assert 'loading="lazy"' in _read("templates/news.html")


def test_above_the_fold_hero_images_are_not_lazy():
    for path, marker in [
        ("templates/artist_profile.html", "crossorigin=\"anonymous\""),
        ("templates/compare.html", 'width="64" height="64"'),
    ]:
        html = _read(path)
        line = next(l for l in html.splitlines() if marker in l)
        assert 'loading="lazy"' not in line


# ── light/dark theme contrast: fix tokens, not scattered selectors ──

def test_light_theme_accent_meets_aa_contrast_for_button_text():
    css = _read("static/css/waveline.css")
    light_block = re.search(r"body\.light \{([^}]*)\}", css, re.S).group(1)
    accent = re.search(r"--wv-accent:\s*#([0-9a-fA-F]{6});", light_block).group(1)
    on_accent = re.search(r"--wv-on-accent:\s*#([0-9a-fA-F]{6});", light_block).group(1)

    def lin(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    def lum(hexcolor):
        r, g, b = int(hexcolor[0:2], 16), int(hexcolor[2:4], 16), int(hexcolor[4:6], 16)
        return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)

    l1, l2 = sorted([lum(accent), lum(on_accent)], reverse=True)
    contrast = (l1 + 0.05) / (l2 + 0.05)
    assert contrast >= 4.5, f"light-theme primary-button text contrast is only {contrast:.2f}:1"


# ── reduced motion (section J) — no prior test file covered this at all ──

def test_global_reduced_motion_rule_forces_near_zero_durations():
    css = _read("static/css/waveline.css")
    block = re.search(r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n\}\n", css, re.S)
    assert block, "expected a global prefers-reduced-motion block"
    body = block.group(1)
    assert "animation-duration: .001ms !important" in body
    assert "transition-duration: .001ms !important" in body
    assert "scroll-behavior: auto !important" in body


def test_reveal_content_is_immediately_visible_under_reduced_motion():
    js = _read("static/js/nav.js")
    body = _fn_body(js, "initReveal")
    # must short-circuit BEFORE setting up an IntersectionObserver, not just
    # animate faster
    guard_pos = body.index("reduceMotion")
    observer_pos = body.index("IntersectionObserver(function")
    assert guard_pos < observer_pos
    assert "is-visible" in body[:observer_pos]


def test_magnetic_buttons_disabled_entirely_under_reduced_motion():
    js = _read("static/js/nav.js")
    body = _fn_body(js, "initMagnetic")
    assert re.match(r"if \(!finePointer \|\| reduceMotion\) return;", body.strip())


def test_notification_badge_uses_theme_aware_on_danger_token_not_hardcoded_white():
    css = _read("static/css/waveline.css")
    assert "--wv-on-danger:" in css  # defined for both :root (dark) and body.light
    badge_rule = re.search(r"\.wv-badge \{([^}]*)\}", css).group(1)
    assert "var(--wv-on-danger)" in badge_rule
    assert "color: #fff" not in badge_rule


# ══════════════════════════════════════════════════════════════════════
# FINAL TEST-ONLY HARDENING PASS (post-approval, pre-commit)
# ══════════════════════════════════════════════════════════════════════

# ── 1. Critical aria-controls / id pairing in the shared shell ──────

def test_every_aria_controls_target_in_base_html_exists_exactly_once():
    html = _read("templates/base.html")
    controls = set(re.findall(r'aria-controls="([^"]+)"', html))
    assert controls, "expected at least one aria-controls in base.html"

    critical = {
        "wv-palette-overlay",   # command palette
        "wv-msg-panel",         # Messages panel
        "wv-notif-panel",       # Notifications panel
        "wv-profile-menu",      # Profile menu
        "wv-navmenu-panel",     # desktop More menu
        "wv-morepanel",         # mobile More panel
    }
    assert critical <= controls, f"missing critical aria-controls targets: {critical - controls}"

    for target_id in controls:
        occurrences = len(re.findall(r'\bid="' + re.escape(target_id) + r'"', html))
        assert occurrences == 1, f'id="{target_id}" appears {occurrences} times in base.html (must be exactly 1)'


def test_no_duplicate_ids_anywhere_in_base_html():
    # Two search triggers legitimately share one aria-controls TARGET
    # (wv-palette-overlay) — that's fine. What must never happen is two
    # elements both claiming the same id.
    html = _read("templates/base.html")
    ids = re.findall(r'\bid="([^"]+)"', html)
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, f"duplicate ids found in base.html: {dupes}"


# ── 2. Icon-only accessible names — standing regression (was a one-off
#      script during the audit; now a real test over every template) ──

def _icon_only_controls_missing_accessible_name(html, tag):
    """Pragmatic, not a full HTML parser: for every <tag ...>...</tag>, strip
    <svg>...</svg> and any aria-hidden="true" span before checking whether
    real visible content remains. Jinja output ({{ ... }}) inside the
    remaining text counts as visible content — we don't evaluate templates,
    we just need to know *something* will render there, and template source
    containing an expression proves that."""
    problems = []
    pattern = r"<" + tag + r"\b[^>]*>((?:(?!</" + tag + r">).)*)</" + tag + r">"
    for m in re.finditer(pattern, html, re.S):
        full, inner = m.group(0), m.group(1)
        if 'aria-label="' in full[: full.index(">") + 1] or "aria-labelledby=" in full[: full.index(">") + 1]:
            continue
        visible = re.sub(r"<svg.*?</svg>", "", inner, flags=re.S)
        visible = re.sub(r'<span[^>]*aria-hidden="true"[^>]*>.*?</span>', "", visible, flags=re.S)
        visible_text = re.sub(r"<[^>]+>", "", visible).strip()
        if not visible_text:
            problems.append(full[:160])
    return problems


def test_icon_only_buttons_have_accessible_names_repo_wide():
    import glob
    failures = {}
    for path in sorted(glob.glob("templates/*.html")):
        problems = _icon_only_controls_missing_accessible_name(_read(path), "button")
        if problems:
            failures[path] = problems
    assert not failures, f"icon-only <button> missing an accessible name: {failures}"


def test_icon_only_links_have_accessible_names_repo_wide():
    import glob
    failures = {}
    for path in sorted(glob.glob("templates/*.html")):
        problems = _icon_only_controls_missing_accessible_name(_read(path), "a")
        if problems:
            failures[path] = problems
    assert not failures, f"icon-only <a> missing an accessible name: {failures}"


# ── 3. Form accessible names — targeted, not a universal parser ─────
# A field is accessibly named if there's a real `<label for="ID">`
# somewhere in the (raw, un-rendered) template source alongside `id="ID"`,
# matching how every one of these forms is already written. All of these
# were already correctly labelled during the Phase 8 audit — these tests
# just lock that in as a standing regression.

def _has_label_for(html, field_id):
    return f'for="{field_id}"' in html and f'id="{field_id}"' in html


def test_login_fields_have_accessible_names():
    html = _read("templates/auth.html")
    assert _has_label_for(html, "identifier")  # username-or-email
    assert _has_label_for(html, "password")


def test_register_fields_have_accessible_names():
    html = _read("templates/auth.html")
    assert _has_label_for(html, "username")
    assert _has_label_for(html, "email")
    assert _has_label_for(html, "password")


def test_settings_fields_have_accessible_names():
    html = _read("templates/settings.html")
    for field_id in ["bio", "location", "website", "genres", "avatar-file", "cover-file"]:
        assert _has_label_for(html, field_id), f"#{field_id} missing a real <label for>"


def test_compare_artist_inputs_have_accessible_names():
    html = _read("templates/compare.html")
    assert _has_label_for(html, "cp-a")
    assert _has_label_for(html, "cp-b")


def test_feed_composer_has_accessible_name():
    html = _read("templates/feed.html")
    assert _has_label_for(html, "cm-body")   # post body textarea
    assert _has_label_for(html, "cm-artist")  # tag-an-artist field


def test_comment_field_has_accessible_name():
    html = _read("templates/_post_card.html")
    assert 'for="comment-{{ post.id }}"' in html
    assert 'id="comment-{{ post.id }}"' in html


def test_direct_message_textarea_has_accessible_name():
    html = _read("templates/messages_thread.html")
    assert _has_label_for(html, "dm-body")


def test_homepage_artist_search_has_accessible_name():
    html = _read("templates/index.html")
    assert _has_label_for(html, "artist-input")


# ── 4. Skip-link focus visibility ────────────────────────────────────

def test_skip_link_has_a_visible_focused_state():
    css = _read("static/css/waveline.css")
    rule = re.search(r"\.wv-skip \{([^}]*)\}", css)
    assert rule, "expected a base .wv-skip rule"
    focus_rule = re.search(r"\.wv-skip:focus \{([^}]*)\}", css)
    assert focus_rule, "expected a .wv-skip:focus rule that moves it on screen"
    # must actually reposition it on focus, not just tweak color — the
    # off-screen technique this relies on is normally a negative
    # left/top offset, so :focus must override that positioning
    assert re.search(r"left:\s*0", focus_rule.group(1)) or re.search(r"top:\s*0", focus_rule.group(1))

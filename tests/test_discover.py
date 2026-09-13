"""Tests for Phase 5: Discover / Who to follow.

Two layers, matching the rest of this suite's conventions:
- data layer (social.py's new discover functions): a fake db_cursor that
  records executed SQL/params and returns canned fetchall() results in call
  order, so we can assert the actual query shape and the Python-level
  combining/ranking logic without ever touching a real database.
- route/template layer (views_discover.py + discover.html): the data
  functions are monkeypatched directly (same pattern as
  test_polish.py::test_notifications_page_renders), so these tests only
  exercise routing, templates, auth and redirect safety.

No network or real database calls anywhere in this file.
"""

import contextlib

import dashboard
import social
import views_discover
from models import User

OWNER = User(id=1, username="dimos", email="d@e.com", password_hash="x")
OTHER = User(id=2, username="alice", email="a@e.com", password_hash="x")


def _login(client, monkeypatch, user=OWNER):
    monkeypatch.setattr(User, "get", classmethod(lambda cls, i: user))
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)


def _sequential_db_cursor(monkeypatch, target, results):
    """`results` is a list of fetchall() return values, consumed one per
    execute() call in the order the code under test issues them."""
    calls = []
    remaining = list(results)

    class FakeCur:
        def execute(self, sql, params=None):
            calls.append((sql, params))

        def fetchall(self):
            return remaining.pop(0) if remaining else []

        def fetchone(self):
            rows = remaining.pop(0) if remaining else []
            return rows[0] if rows else None

    @contextlib.contextmanager
    def cm(commit=False):
        yield FakeCur()

    monkeypatch.setattr(target, "db_cursor", cm)
    return calls


def _person(id=1, username="alice", bio="", genres="", profile_image_url="",
            follower_count=0, following=False, shared_count=0, shared_artists=None):
    return {
        "id": id, "username": username, "bio": bio, "genres": genres,
        "genre_list": [g.strip() for g in genres.split(",") if g.strip()],
        "profile_image_url": profile_image_url, "follower_count": follower_count,
        "following": following, "shared_count": shared_count,
        "shared_artists": shared_artists or [],
    }


def _mock_discover_route(monkeypatch, people_for_you=None, community=None, search_results=None):
    monkeypatch.setattr(views_discover, "get_people_for_you", lambda uid, limit=8: people_for_you or [])
    monkeypatch.setattr(views_discover, "get_community_suggestions",
                         lambda *a, **k: community or [])
    monkeypatch.setattr(views_discover, "search_people",
                         lambda q, viewer_id=None, limit=20: search_results or [])


# ── social.py data layer: overlap, ranking, normalisation ───────────

def test_get_viewer_artist_set_normalises_case_and_whitespace(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, social, [[("radiohead",), ("sza",)]])
    result = social.get_viewer_artist_set(7)
    assert result == ["radiohead", "sza"]
    sql = calls[0][0]
    assert "LOWER(TRIM(artist_name))" in sql
    assert calls[0][1] == (7,)


def test_get_people_for_you_overlap_query_excludes_self_and_followed():
    import inspect
    src = inspect.getsource(social.get_people_for_you)
    assert "u.id != %(viewer)s" in src
    assert "NOT EXISTS (SELECT 1 FROM follows f2" in src
    assert "LOWER(TRIM(s.artist_name)) = ANY(%(artists)s)" in src
    assert "COUNT(DISTINCT LOWER(TRIM(s.artist_name)))" in src
    assert "ORDER BY shared_count DESC, follower_count DESC" in src


def test_shared_candidates_are_ordered_before_fallback_padding(monkeypatch):
    _sequential_db_cursor(monkeypatch, social, [
        [("radiohead",)],                              # viewer_artists
        [(2, "alice", "", "", "", 1, 0)],               # 1 overlap candidate
        [(3, "bob", "", "", "", 0, False)],             # community padding (id, username, bio, genres, img, followers, following)
        [(2, "Radiohead")],                             # shared-artist names for candidate 2
    ])
    result = social.get_people_for_you(1, limit=2)
    assert len(result) == 2
    assert result[0]["id"] == 2 and result[0]["shared_count"] == 1
    assert result[1]["id"] == 3 and result[1]["shared_count"] == 0
    assert result[0]["shared_artists"] == ["Radiohead"]
    assert result[1]["shared_artists"] == []


def test_no_history_user_skips_overlap_query_and_gets_fallback(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, social, [
        [],                                    # viewer_artists: no search history
        [(3, "bob", "", "", "", 0, False)],    # straight to community fallback
    ])
    result = social.get_people_for_you(1, limit=8)
    assert len(calls) == 2  # viewer_artists + community only — overlap query skipped entirely
    assert len(result) == 1
    assert result[0]["shared_count"] == 0
    assert result[0]["shared_artists"] == []


def test_shared_artist_names_groups_by_user_and_dedupes(monkeypatch):
    _sequential_db_cursor(monkeypatch, social, [
        [(2, "Bonobo"), (2, "Four Tet"), (5, "SZA")],
    ])
    grouped = social._shared_artist_names([2, 5], ["bonobo", "four tet", "sza"])
    assert grouped[2] == ["Bonobo", "Four Tet"]
    assert grouped[5] == ["SZA"]


def test_shared_artist_names_short_circuits_without_a_query(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, social, [[]])
    assert social._shared_artist_names([], ["radiohead"]) == {}
    assert social._shared_artist_names([2], []) == {}
    assert calls == []  # neither call should have touched the database at all


def test_get_community_suggestions_can_exclude_already_followed(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, social, [[(3, "bob", "", "", "", 2, False)]])
    result = social.get_community_suggestions([1], limit=5, viewer_id=1, exclude_followed=True)
    assert result[0]["id"] == 3
    params = calls[0][1]
    assert params["exclude_followed"] is True
    assert params["viewer"] == 1
    assert params["exclude"] == [1]


def test_search_people_lowercases_and_caps_query(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, social, [[]])
    social.search_people("  SZA  " + "x" * 100, viewer_id=9, limit=20)
    params = calls[0][1]
    assert params["pattern"].islower() or params["pattern"] == params["pattern"].lower()
    assert len(params["pattern"]) <= 84  # 80-char cap + the two '%' wildcards
    assert params["viewer"] == 9


def test_search_people_sql_excludes_email_and_self():
    import inspect
    src = inspect.getsource(social.search_people)
    assert "u.email" not in src
    assert "u.id != %(viewer)s" in src
    assert "LOWER(u.username) LIKE" in src


def test_search_people_empty_query_returns_empty_without_a_query(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, social, [[("should", "not", "be", "used")]])
    assert social.search_people("   ") == []
    assert calls == []


# ── /discover route ──────────────────────────────────────────────────

def test_discover_renders_successfully_anonymous(monkeypatch):
    _mock_discover_route(monkeypatch, community=[_person()])
    client = dashboard.app.test_client()
    resp = client.get("/discover")
    assert resp.status_code == 200
    assert b"Discover people" in resp.data


def test_discover_renders_successfully_authenticated(monkeypatch):
    _mock_discover_route(monkeypatch, people_for_you=[_person(shared_count=1, shared_artists=["SZA"])])
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.get("/discover")
    assert resp.status_code == 200
    assert b"People for you" in resp.data


def test_current_user_excluded_via_exclude_ids_passed_to_community_query(monkeypatch):
    seen = {}
    monkeypatch.setattr(views_discover, "get_people_for_you", lambda uid, limit=8: [_person(id=2)])

    def fake_community(exclude_ids=None, limit=12, viewer_id=None, exclude_followed=False):
        seen["exclude_ids"] = exclude_ids
        seen["viewer_id"] = viewer_id
        return []

    monkeypatch.setattr(views_discover, "get_community_suggestions", fake_community)
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    client.get("/discover")
    assert OWNER.id in seen["exclude_ids"]
    assert seen["viewer_id"] == OWNER.id


def test_all_people_for_you_ids_are_excluded_from_community_query(monkeypatch):
    # Item 2 hardening: every id already shown in "People for you" (not just
    # the viewer) must be excluded from the "Explore the community" query,
    # so the same account can never render in both sections.
    seen = {}
    monkeypatch.setattr(views_discover, "get_people_for_you",
                         lambda uid, limit=8: [_person(id=2), _person(id=3)])

    def fake_community(exclude_ids=None, limit=12, viewer_id=None, exclude_followed=False):
        seen["exclude_ids"] = set(exclude_ids)
        return []

    monkeypatch.setattr(views_discover, "get_community_suggestions", fake_community)
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    client.get("/discover")
    assert seen["exclude_ids"] == {OWNER.id, 2, 3}


def test_authenticated_page_never_renders_same_person_in_both_sections(monkeypatch):
    # End-to-end proof through the real social.py functions (only the DB
    # cursor is faked): a person who lands in "People for you" via overlap
    # or fallback padding must never also appear in "Explore the community",
    # even though both sections are ultimately backed by the same
    # get_community_suggestions() query.
    _sequential_db_cursor(monkeypatch, social, [
        [("radiohead",)],                                # 1. viewer_artists
        [(2, "alice", "", "", "", 1, 0)],                 # 2. overlap candidate (shared_count=1)
        [(3, "bob", "", "", "", 0, False)],                # 3. fallback padding into People for you
        [(2, "Radiohead")],                                # 4. shared-artist names
        [(4, "carol", "", "", "", 0, False)],              # 5. Explore the community query
    ])
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    html = client.get("/discover").data.decode()

    pfy_start = html.index("People for you")
    community_start = html.index("Explore the community")
    pfy_block = html[pfy_start:community_start]
    community_block = html[community_start:]

    assert "/u/alice" in pfy_block and "/u/bob" in pfy_block
    assert "/u/carol" in community_block
    # the two ids already shown in People for you must not also appear below
    assert "/u/alice" not in community_block
    assert "/u/bob" not in community_block
    assert "/u/carol" not in pfy_block


def test_no_history_shows_gentle_fallback_hint(monkeypatch):
    _mock_discover_route(monkeypatch, people_for_you=[_person(shared_count=0)])
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/discover").data.decode()
    assert "Explore more artists to make your recommendations more personal." in html


def test_personalised_recommendations_do_not_show_fallback_hint(monkeypatch):
    _mock_discover_route(monkeypatch, people_for_you=[_person(shared_count=2, shared_artists=["SZA", "Bonobo"])])
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/discover").data.decode()
    assert "Explore more artists" not in html
    assert "2</strong> artist" in html
    assert "SZA" in html and "Bonobo" in html


def test_no_one_to_discover_empty_state(monkeypatch):
    _mock_discover_route(monkeypatch)  # everything empty
    client = dashboard.app.test_client()
    resp = client.get("/discover")
    assert b"No one to discover yet" in resp.data
    assert b"People for you" not in resp.data


def test_search_query_renders_results(monkeypatch):
    _mock_discover_route(monkeypatch, search_results=[_person(username="bonobo_fan")])
    client = dashboard.app.test_client()
    resp = client.get("/discover?q=bonobo")
    html = resp.data.decode()
    assert "bonobo_fan" in html
    assert "Results for &quot;bonobo&quot;" in html


def test_search_no_results_shows_message(monkeypatch):
    _mock_discover_route(monkeypatch, search_results=[])
    client = dashboard.app.test_client()
    html = client.get("/discover?q=zzzznotfound").data.decode()
    assert "No people found for" in html
    assert "zzzznotfound" in html


def test_search_query_is_escaped_in_html(monkeypatch):
    _mock_discover_route(monkeypatch, search_results=[])
    client = dashboard.app.test_client()
    html = client.get("/discover?q=%3Cscript%3Ealert(1)%3C/script%3E").data.decode()
    assert "<script>alert(1)</script>" not in html


def test_search_q_is_capped_to_80_chars(monkeypatch):
    seen = {}
    monkeypatch.setattr(views_discover, "search_people",
                         lambda q, viewer_id=None, limit=20: seen.setdefault("q", q) or [])
    client = dashboard.app.test_client()
    client.get("/discover?q=" + "a" * 500)
    assert len(seen["q"]) == 80


def test_discover_never_renders_email(monkeypatch):
    user_with_email = _person(username="hasemail")
    _mock_discover_route(monkeypatch, community=[user_with_email])
    client = dashboard.app.test_client()
    html = client.get("/discover").data.decode()
    assert "d@e.com" not in html
    assert "a@e.com" not in html
    assert "@e.com" not in html


def test_discover_card_renders_real_profile_image(monkeypatch):
    _mock_discover_route(monkeypatch, community=[
        _person(profile_image_url="https://res.cloudinary.com/demo/alice.jpg")
    ])
    client = dashboard.app.test_client()
    html = client.get("/discover").data.decode()
    assert '<img class="wv-avatar-sm" src="https://res.cloudinary.com/demo/alice.jpg"' in html


def test_discover_avatar_has_exactly_one_wrapper_with_genuinely_hidden_fallback(monkeypatch):
    # Regression: the exact Phase 3 bug (image + monogram rendering side by
    # side) happened because a fallback element relied on the `hidden`
    # attribute, which the .wv-avatar class's own `display: inline-flex`
    # rule silently overrides (an author-stylesheet class rule beats the UA
    # default `[hidden] { display: none }` at equal specificity). The
    # Discover card must use a genuine inline `display:none` instead, exactly
    # like templates/profile.html's `#pf-avatar-fallback`, with both the
    # <img> and its fallback living inside a single .dc-avatar wrapper.
    _mock_discover_route(monkeypatch, community=[
        _person(id=42, username="hasimage", profile_image_url="https://res.cloudinary.com/demo/hasimage.jpg")
    ])
    client = dashboard.app.test_client()
    html = client.get("/discover").data.decode()

    assert html.count('class="dc-avatar"') == 1
    avatar_start = html.index('class="dc-avatar"')
    avatar_end = html.index("</div>", avatar_start)
    avatar_block = html[avatar_start:avatar_end]

    assert "<img" in avatar_block
    assert 'id="dc-fallback-42"' in avatar_block
    assert "display:none" in avatar_block or "display: none" in avatar_block
    # the fallback must not rely solely on the `hidden` attribute
    assert " hidden" not in avatar_block.split('id="dc-fallback-42"')[1].split(">")[0]
    # onerror must hide the image and reveal the SAME card's fallback by id
    assert "onerror=\"this.style.display='none'; document.getElementById('dc-fallback-42').style.display='inline-flex';\"" in avatar_block


def test_discover_card_falls_back_to_monogram_without_image(monkeypatch):
    _mock_discover_route(monkeypatch, community=[_person(id=7, username="nopic", profile_image_url="")])
    client = dashboard.app.test_client()
    html = client.get("/discover").data.decode()
    assert html.count('class="dc-avatar"') == 1
    card_start = html.index('href="/u/nopic"')
    card_block = html[card_start - 400:card_start + 100]
    assert 'class="wv-avatar wv-avatar-sm"' in card_block
    assert "<img" not in card_block
    assert "dc-fallback-" not in card_block  # no unused fallback markup when there's no image at all


def test_discover_clean_username_heading_no_at_prefix(monkeypatch):
    _mock_discover_route(monkeypatch, community=[_person(username="BlakeNor")])
    client = dashboard.app.test_client()
    html = client.get("/discover").data.decode()
    assert '<a class="dc-name" href="/u/BlakeNor">BlakeNor</a>' in html
    assert ">@BlakeNor<" not in html


# ── POST /discover/u/<username>/follow ──────────────────────────────

def test_discover_follow_requires_login():
    client = dashboard.app.test_client()
    resp = client.post("/discover/u/alice/follow")
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("Location", "")


def test_discover_follow_uses_current_user_not_client_supplied_id(monkeypatch):
    seen = {}
    monkeypatch.setattr(User, "get_by_username",
                         classmethod(lambda cls, u: OTHER if u == "alice" else None))
    monkeypatch.setattr(views_discover, "toggle_follow",
                         lambda follower_id, followee_id: seen.update(follower=follower_id, followee=followee_id))
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    client.post("/discover/u/alice/follow", data={"user_id": "999"})
    assert seen["follower"] == OWNER.id
    assert seen["followee"] == OTHER.id


def test_discover_self_follow_is_impossible(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(User, "get_by_username", classmethod(lambda cls, u: OWNER if u == "dimos" else None))
    monkeypatch.setattr(views_discover, "toggle_follow", lambda a, b: called.update(n=called["n"] + 1))
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    client.post("/discover/u/dimos/follow")
    assert called["n"] == 0


def test_discover_follow_reuses_social_toggle_follow_not_a_duplicate():
    assert views_discover.toggle_follow is social.toggle_follow


def test_discover_follow_and_unfollow_both_call_toggle_once(monkeypatch):
    calls = []
    monkeypatch.setattr(User, "get_by_username", classmethod(lambda cls, u: OTHER if u == "alice" else None))
    monkeypatch.setattr(views_discover, "toggle_follow", lambda a, b: calls.append((a, b)))
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    client.post("/discover/u/alice/follow")
    client.post("/discover/u/alice/follow")
    assert calls == [(OWNER.id, OTHER.id), (OWNER.id, OTHER.id)]


def test_discover_follow_preserves_search_query_safely(monkeypatch):
    monkeypatch.setattr(User, "get_by_username", classmethod(lambda cls, u: OTHER if u == "alice" else None))
    monkeypatch.setattr(views_discover, "toggle_follow", lambda a, b: None)
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    resp = client.post("/discover/u/alice/follow", data={"q": "jazz heads"})
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/discover?q=jazz+heads"


def test_discover_follow_without_query_redirects_plain(monkeypatch):
    monkeypatch.setattr(User, "get_by_username", classmethod(lambda cls, u: OTHER if u == "alice" else None))
    monkeypatch.setattr(views_discover, "toggle_follow", lambda a, b: None)
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    resp = client.post("/discover/u/alice/follow")
    assert resp.headers["Location"] == "/discover"


def test_discover_follow_has_no_generic_open_redirect():
    import inspect
    src = inspect.getsource(views_discover)
    assert 'request.form.get("next"' not in src
    assert 'request.args.get("next"' not in src
    assert "request.form.get(\"q\"" in src


# ── navigation / footer / command palette ───────────────────────────

def test_desktop_nav_contains_discover():
    client = dashboard.app.test_client()
    html = client.get("/about").data.decode()
    nav = html[html.index('<nav class="wv-nav"'):html.index("</nav>")]
    assert ">Discover<" in nav
    assert 'href="/discover"' in nav


def test_mobile_bottom_nav_contains_discover_not_compare():
    client = dashboard.app.test_client()
    html = client.get("/about").data.decode()
    start = html.index('class="wv-bottomnav"')
    end = html.index("</nav>", start)
    bottomnav = html[start:end]
    assert ">Discover<" in bottomnav
    assert ">Compare<" not in bottomnav


def test_compare_still_reachable_from_mobile_more():
    client = dashboard.app.test_client()
    html = client.get("/about").data.decode()
    more_panel = html[html.index('id="wv-morepanel"'):html.index('id="wv-palette-overlay"')]
    assert 'href="/compare"' in more_panel


def test_footer_contains_discover():
    client = dashboard.app.test_client()
    html = client.get("/about").data.decode()
    footer = html[html.index('class="wv-footer-inner"'):html.index("</footer>")]
    assert 'href="/discover"' in footer


def test_command_palette_contains_discover():
    with open("static/js/command-palette.js") as f:
        js = f.read()
    assert '"Discover people"' in js
    assert '"/discover"' in js


# ── regression: existing profile-follow route unaffected ───────────

def test_existing_profile_follow_route_still_works(monkeypatch):
    import profiles
    seen = {}
    monkeypatch.setattr(User, "get_by_username", classmethod(lambda cls, u: OTHER if u == "alice" else None))
    monkeypatch.setattr(profiles, "toggle_follow", lambda a, b: seen.update(a=a, b=b))
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    resp = client.post("/u/alice/follow")
    assert resp.status_code == 302
    assert "/u/alice" in resp.headers["Location"]
    assert seen == {"a": OWNER.id, "b": OTHER.id}

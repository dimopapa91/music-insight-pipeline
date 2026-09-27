"""Profile: minimal artist collection + social links (27 Sep 2026)."""

import contextlib
import datetime

import pytest

import dashboard
import profiles
import social_links
from test_profile_redesign import OWNER, _login, _mock_profile_data


# ── normalisation ───────────────────────────────────────────────────

@pytest.mark.parametrize("platform,raw,expected", [
    ("instagram", "dimos.papa", "https://instagram.com/dimos.papa"),
    ("instagram", "@dimos.papa", "https://instagram.com/dimos.papa"),
    ("instagram", "https://www.instagram.com/dimos.papa/", "https://www.instagram.com/dimos.papa"),
    ("soundcloud", "blakenor", "https://soundcloud.com/blakenor"),
    ("x", "https://twitter.com/blakenor", "https://twitter.com/blakenor"),
    ("x", "@blakenor", "https://x.com/blakenor"),
    ("discord", "blakenor", "blakenor"),
    ("discord", "https://discord.gg/abc123", "https://discord.gg/abc123"),
    ("spotify", "https://open.spotify.com/artist/3abc", "https://open.spotify.com/artist/3abc"),
    ("youtube", "@blakenor", "https://youtube.com/@blakenor"),
    ("bandcamp", "blakenor", "https://blakenor.bandcamp.com"),
    ("tiktok", "blakenor", "https://tiktok.com/@blakenor"),
    ("instagram", "", ""),
])
def test_normalize_valid(platform, raw, expected):
    assert social_links.normalize(platform, raw) == expected


@pytest.mark.parametrize("platform,raw", [
    ("instagram", "https://evil.example/dimos"),
    ("instagram", "javascript:alert(1)"),
    ("instagram", "has space"),
    ("spotify", "blakenor"),                      # needs a full link
    ("x", "https://x.com/"),                      # site, not a profile
])
def test_normalize_rejects(platform, raw):
    with pytest.raises(social_links.LinkError):
        social_links.normalize(platform, raw)


# ── profile page ────────────────────────────────────────────────────

def test_collection_is_capped_with_show_all(monkeypatch):
    names = [f"Artist {i}" for i in range(12)]
    _mock_profile_data(monkeypatch, artists=names)
    monkeypatch.setattr(social_links, "get_links", lambda uid: {})
    html = dashboard.app.test_client().get("/u/dimos").data.decode()
    visible = html[html.index('class="pf-artist-list"'):html.index('class="pf-more"')]
    assert visible.count("pf-artist-chip") == profiles.ARTISTS_SHOWN
    assert "Show all 12" in html
    assert "pf-artist-tile" not in html


def test_searched_artists_deduplicated_most_recent_first(monkeypatch):
    rows = [("SZA", datetime.datetime(2026, 9, 3)), ("Sza", datetime.datetime(2026, 9, 2)),
            ("Ami  X", datetime.datetime(2026, 9, 1)), ("Ami X", datetime.datetime(2026, 8, 1))]

    class Cur:
        def execute(self, *a, **k):
            pass

        def fetchall(self):
            return rows

    @contextlib.contextmanager
    def cm(commit=False):
        yield Cur()
    monkeypatch.setattr(profiles, "db_cursor", cm)
    assert profiles.get_user_searched_artists(1) == ["SZA", "Ami  X"]


def test_links_row_renders_buttons_and_discord_as_text(monkeypatch):
    _mock_profile_data(monkeypatch, artists=["Radiohead"])
    monkeypatch.setattr(social_links, "get_links", lambda uid: {
        "instagram": "https://instagram.com/dimos.papa", "discord": "blakenor"})
    html = dashboard.app.test_client().get("/u/dimos").data.decode()
    row = html[html.index('class="pf-links"'):html.index("</ul>", html.index('class="pf-links"'))]
    assert 'href="https://instagram.com/dimos.papa"' in row
    assert 'rel="nofollow noopener noreferrer"' in row
    assert "cdn.simpleicons.org/instagram" in row
    assert "Discord · blakenor" in row
    assert 'href="https://dimospapageorgiou.com"' in row          # website joins the same row
    assert row.index("Instagram") < row.index("Discord") < row.index("Website")


def test_owner_without_links_sees_add_prompt(monkeypatch):
    _mock_profile_data(monkeypatch)
    monkeypatch.setattr(social_links, "get_links", lambda uid: {})
    no_site = type(OWNER)(id=1, username="dimos", email="d@e.com", password_hash="x")
    import models
    monkeypatch.setattr(models.User, "get_by_username", classmethod(lambda cls, u: no_site))
    client = dashboard.app.test_client()
    _login(client, monkeypatch, no_site)
    html = client.get("/u/dimos").data.decode()
    assert 'href="/settings#links"' in html


# ── settings ────────────────────────────────────────────────────────

def test_settings_saves_valid_links_and_flags_invalid(monkeypatch):
    import models
    saved = {}
    monkeypatch.setattr(models.User, "update_profile", lambda self, *a, **k: None)
    monkeypatch.setattr(social_links, "get_links", lambda uid: {"x": "https://x.com/old"})
    monkeypatch.setattr(social_links, "save_links", lambda uid, d: saved.update(d))
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    resp = client.post("/settings", data={"bio": "", "link_instagram": "@dimos.papa",
                                          "link_x": "https://evil.example/x",
                                          "link_soundcloud": ""})
    html = resp.data.decode()
    assert saved["instagram"] == "https://instagram.com/dimos.papa"
    assert saved["x"] == "https://x.com/old"                     # invalid entry keeps old value
    assert saved["soundcloud"] == ""
    assert "isn&#39;t a link to X" in html or "isn't a link to X" in html
    assert 'value="https://evil.example/x"' in html              # what they typed stays in the box


def test_settings_form_lists_every_platform(monkeypatch):
    monkeypatch.setattr(social_links, "get_links", lambda uid: {})
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    html = client.get("/settings").data.decode()
    for key in social_links.PLATFORMS:
        assert f'name="link_{key}"' in html
    assert 'id="links"' in html


def test_website_that_is_a_platform_link_shows_as_that_platform():
    import social_links as sl
    orig = sl.get_links
    try:
        sl.get_links = lambda uid: {}
        out = sl.links_for_display(1, "https://open.spotify.com/artist/5RP")
        assert out == [{"platform": "spotify", "label": "Spotify", "href": "https://open.spotify.com/artist/5RP",
                        "text": "open.spotify.com/artist/5RP", "icon": "spotify"}]
        sl.get_links = lambda uid: {"spotify": "https://open.spotify.com/artist/other"}
        out = sl.links_for_display(1, "https://open.spotify.com/artist/5RP")
        assert [l["platform"] for l in out] == ["spotify"]      # no duplicate Spotify button
        out = sl.links_for_display(1, "dimospapageorgiou.com")
        assert out[-1]["label"] == "Website" and out[-1]["href"] == "https://dimospapageorgiou.com"
    finally:
        sl.get_links = orig

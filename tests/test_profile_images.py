"""Integration tests for Phase 3 (profile photo / cover image uploads).

Cloudinary is never called for real here — `profiles.upload_profile_image` /
`profiles.upload_cover_image` (the names imported into profiles.py's own
namespace) are monkeypatched directly, exactly like every other external
integration in this test suite.
"""

import io

import dashboard
import profiles
from image_storage import ImageValidationError, ImageStorageError
from models import User

OWNER = User(id=1, username="dimos", email="d@e.com", password_hash="x")


def _login(client, monkeypatch, user=OWNER):
    monkeypatch.setattr(User, "get", classmethod(lambda cls, i: user))
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)


def _mock_profile_route(monkeypatch, user):
    monkeypatch.setattr(profiles, "get_user_searched_artists", lambda uid: [])
    monkeypatch.setattr(profiles, "get_follow_counts", lambda uid: (0, 0))
    monkeypatch.setattr(profiles, "get_user_posts", lambda uid, viewer_id=None, **k: [])
    monkeypatch.setattr(profiles, "is_following", lambda a, b: False)
    monkeypatch.setattr(User, "get_by_username",
                         classmethod(lambda cls, u: user if u == user.username else None))


def _fake_file(name="photo.jpg", content=b"not-a-real-check-mocked-anyway", mimetype="image/jpeg"):
    return (io.BytesIO(content), name)


# ── 1/2/3: fallback vs. real image rendering on the public profile ──

def test_profile_without_images_still_shows_monogram_and_css_cover(monkeypatch):
    user = User(id=1, username="dimos", email="d@e.com", password_hash="x")
    _mock_profile_route(monkeypatch, user)
    client = dashboard.app.test_client()
    html = client.get("/u/dimos").data.decode()
    assert 'class="pf-avatar"' in html
    assert 'class="wv-avatar wv-avatar-hero"' in html
    assert "<img" not in html.split('id="overview"')[1].split("</header>")[0]
    assert '<div class="pf-hero"' in html


def test_profile_with_profile_image_renders_real_img(monkeypatch):
    user = User(id=1, username="dimos", email="d@e.com", password_hash="x",
                profile_image_url="https://res.cloudinary.com/demo/image/upload/v1/waveline/users/1/profile.jpg")
    _mock_profile_route(monkeypatch, user)
    client = dashboard.app.test_client()
    html = client.get("/u/dimos").data.decode()
    assert 'src="https://res.cloudinary.com/demo/image/upload/v1/waveline/users/1/profile.jpg"' in html
    assert '<img class="wv-avatar-hero"' in html


def test_profile_avatar_has_exactly_one_wrapper_and_hidden_fallback(monkeypatch):
    # Regression: .pf-avatar used to be applied to the <img> AND the
    # fallback <span> as two separate flex items of the (display:flex)
    # .pf-identity row, and the fallback relied on the `hidden` attribute —
    # which the .wv-avatar class's own `display: inline-flex` rule silently
    # overrides (an author-stylesheet class rule beats the UA default
    # `[hidden] { display: none }` at equal specificity). Net effect: both
    # the real photo and the monogram rendered side by side. Now there must
    # be exactly one .pf-avatar wrapper, and the fallback must be hidden via
    # a real inline `display:none` (which no class rule can silently beat).
    user = User(id=1, username="dimos", email="d@e.com", password_hash="x",
                profile_image_url="https://res.cloudinary.com/demo/image/upload/v1/waveline/users/1/profile.jpg")
    _mock_profile_route(monkeypatch, user)
    client = dashboard.app.test_client()
    html = client.get("/u/dimos").data.decode()

    assert html.count('class="pf-avatar"') == 1
    avatar_start = html.index('class="pf-avatar"')
    avatar_end = html.index("</div>", avatar_start)
    avatar_block = html[avatar_start:avatar_end]

    assert "<img" in avatar_block
    assert 'id="pf-avatar-fallback"' in avatar_block
    assert "display:none" in avatar_block or "display: none" in avatar_block
    # the fallback must not rely solely on the `hidden` attribute
    assert " hidden" not in avatar_block.split('id="pf-avatar-fallback"')[1].split(">")[0]


def test_profile_with_cover_image_renders_real_img(monkeypatch):
    user = User(id=1, username="dimos", email="d@e.com", password_hash="x",
                cover_image_url="https://res.cloudinary.com/demo/image/upload/v1/waveline/users/1/cover.jpg")
    _mock_profile_route(monkeypatch, user)
    client = dashboard.app.test_client()
    html = client.get("/u/dimos").data.decode()
    assert 'class="pf-hero-img"' in html
    assert 'src="https://res.cloudinary.com/demo/image/upload/v1/waveline/users/1/cover.jpg"' in html


# ── 4/5: navbar avatar ──

def test_navbar_shows_uploaded_photo_when_present(monkeypatch):
    user = User(id=1, username="dimos", email="d@e.com", password_hash="x",
                profile_image_url="https://res.cloudinary.com/demo/image/upload/v1/waveline/users/1/profile.jpg")
    client = dashboard.app.test_client()
    _login(client, monkeypatch, user)
    html = client.get("/about").data.decode()
    dock = html[html.index('<header class="wv-header"'):html.index("</header>")]
    assert '<img class="wv-profile-avatar"' in dock
    assert 'src="https://res.cloudinary.com/demo/image/upload/v1/waveline/users/1/profile.jpg"' in dock


def test_navbar_falls_back_to_monogram_without_photo(monkeypatch):
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    html = client.get("/about").data.decode()
    dock = html[html.index('<header class="wv-header"'):html.index("</header>")]
    assert '<span class="wv-profile-avatar"' in dock
    assert "<img" not in dock[dock.index('id="wv-profile-trigger"'):dock.index("wv-profilemenu-panel")]


# ── 6: settings page has both upload controls ──

def test_settings_page_has_avatar_and_cover_upload_controls(monkeypatch):
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    html = client.get("/settings").data.decode()
    assert 'action="/settings/avatar"' in html
    assert 'action="/settings/cover"' in html
    assert 'enctype="multipart/form-data"' in html
    assert 'id="avatar-file"' in html
    assert 'id="cover-file"' in html
    # existing text-profile fields must still be present
    assert 'name="bio"' in html
    assert 'name="location"' in html
    assert 'name="website"' in html
    assert 'name="genres"' in html


# ── 7: upload routes require authentication ──

def test_avatar_upload_requires_login():
    client = dashboard.app.test_client()
    resp = client.post("/settings/avatar", data={"image": _fake_file()}, content_type="multipart/form-data")
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("Location", "")


def test_cover_upload_requires_login():
    client = dashboard.app.test_client()
    resp = client.post("/settings/cover", data={"image": _fake_file()}, content_type="multipart/form-data")
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("Location", "")


# ── 8/9: valid mocked uploads store the returned secure URL ──

def test_valid_avatar_upload_stores_secure_url(monkeypatch):
    seen = {}

    def fake_upload(uid, f):
        seen["upload_uid"] = uid
        return "https://res.cloudinary.com/demo/profile-new.jpg"

    monkeypatch.setattr(profiles, "upload_profile_image", fake_upload)
    monkeypatch.setattr(User, "update_profile_image", lambda self, url: seen.__setitem__("saved_url", url))
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    resp = client.post("/settings/avatar", data={"image": _fake_file()}, content_type="multipart/form-data")
    assert resp.status_code == 302
    assert "uploaded=avatar" in resp.headers["Location"]
    assert seen["upload_uid"] == OWNER.id
    assert seen["saved_url"] == "https://res.cloudinary.com/demo/profile-new.jpg"


def test_valid_cover_upload_stores_secure_url(monkeypatch):
    seen = {}

    def fake_upload(uid, f):
        seen["upload_uid"] = uid
        return "https://res.cloudinary.com/demo/cover-new.jpg"

    monkeypatch.setattr(profiles, "upload_cover_image", fake_upload)
    monkeypatch.setattr(User, "update_cover_image", lambda self, url: seen.__setitem__("saved_url", url))
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    resp = client.post("/settings/cover", data={"image": _fake_file()}, content_type="multipart/form-data")
    assert resp.status_code == 302
    assert "uploaded=cover" in resp.headers["Location"]
    assert seen["saved_url"] == "https://res.cloudinary.com/demo/cover-new.jpg"


# ── 10/11: invalid content / oversized upload rejected ──

def test_invalid_extension_is_rejected(monkeypatch):
    def fake_upload(uid, f):
        raise ImageValidationError("Please upload a JPG, PNG or WebP image.")
    monkeypatch.setattr(profiles, "upload_profile_image", fake_upload)
    saved = {"called": False}
    monkeypatch.setattr(User, "update_profile_image", lambda self, url: saved.update(called=True))
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    resp = client.post("/settings/avatar",
                        data={"image": _fake_file(name="malware.exe", mimetype="application/octet-stream")},
                        content_type="multipart/form-data")
    assert resp.status_code == 302
    assert "upload_error=" in resp.headers["Location"]
    assert saved["called"] is False


def test_oversized_upload_is_rejected(monkeypatch):
    def fake_upload(uid, f):
        raise ImageValidationError("That image is too large (max 5 MB).")
    monkeypatch.setattr(profiles, "upload_profile_image", fake_upload)
    saved = {"called": False}
    monkeypatch.setattr(User, "update_profile_image", lambda self, url: saved.update(called=True))
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    resp = client.post("/settings/avatar", data={"image": _fake_file()}, content_type="multipart/form-data")
    assert resp.status_code == 302
    assert "upload_error=" in resp.headers["Location"]
    assert saved["called"] is False


# ── 12: failed Cloudinary upload preserves the existing DB URL ──

def test_failed_upload_preserves_existing_image_url(monkeypatch):
    def fake_upload(uid, f):
        raise ImageStorageError("We couldn't upload your image right now. Please try again.")
    monkeypatch.setattr(profiles, "upload_profile_image", fake_upload)
    saved = {"called": False}
    monkeypatch.setattr(User, "update_profile_image", lambda self, url: saved.update(called=True))
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    resp = client.post("/settings/avatar", data={"image": _fake_file()}, content_type="multipart/form-data")
    assert resp.status_code == 302
    assert "upload_error=" in resp.headers["Location"]
    # update_profile_image() was never called — whatever URL was already on
    # the row (or lack thereof) is left completely alone.
    assert saved["called"] is False


# ── 13: existing text-profile editing still works ──

def test_text_profile_editing_still_works(monkeypatch):
    seen = {}
    monkeypatch.setattr(User, "update_profile",
                         lambda self, bio, location="", website="", genres="":
                         seen.update(bio=bio, location=location, website=website, genres=genres))
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    resp = client.post("/settings", data={
        "bio": "Jazz and modular synths.",
        "location": "Manchester, UK",
        "website": "https://example.com",
        "genres": "jazz, ambient",
    })
    assert resp.status_code == 200
    assert b"Saved" in resp.data
    assert seen == {
        "bio": "Jazz and modular synths.", "location": "Manchester, UK",
        "website": "https://example.com", "genres": "jazz, ambient",
    }

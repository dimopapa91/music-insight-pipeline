"""Unit tests for image_storage.py — validation logic and configuration
detection only. No real Cloudinary network call is ever made here; upload
tests that need `_upload()` mock `cloudinary.uploader.upload` directly.
"""

import io

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

import image_storage as img_storage


def _make_image_file(fmt="JPEG", size=(20, 20), filename=None, mimetype=None):
    """A genuine, tiny, in-memory image — real bytes, not just a filename."""
    buf = io.BytesIO()
    Image.new("RGB", size, color=(120, 60, 200)).save(buf, format=fmt)
    buf.seek(0)
    ext = fmt.lower() if fmt != "JPEG" else "jpg"
    return FileStorage(
        stream=buf,
        filename=filename or f"photo.{ext}",
        content_type=mimetype or f"image/{fmt.lower()}",
    )


# ── is_image_storage_configured ──

def test_not_configured_when_env_var_absent(monkeypatch):
    monkeypatch.delenv("CLOUDINARY_URL", raising=False)
    assert img_storage.is_image_storage_configured() is False


def test_configured_when_env_var_present(monkeypatch):
    monkeypatch.setenv("CLOUDINARY_URL", "cloudinary://key:secret@demo")
    assert img_storage.is_image_storage_configured() is True


# ── validate_image: accepted formats ──

def test_validate_accepts_real_jpeg():
    f = _make_image_file(fmt="JPEG")
    img_storage.validate_image(f, img_storage.MAX_PROFILE_IMAGE_BYTES)  # must not raise


def test_validate_accepts_real_png():
    f = _make_image_file(fmt="PNG", filename="photo.png", mimetype="image/png")
    img_storage.validate_image(f, img_storage.MAX_PROFILE_IMAGE_BYTES)


def test_validate_accepts_real_webp():
    f = _make_image_file(fmt="WEBP", filename="photo.webp", mimetype="image/webp")
    img_storage.validate_image(f, img_storage.MAX_PROFILE_IMAGE_BYTES)


def test_validate_resets_stream_position_after_success():
    f = _make_image_file(fmt="JPEG")
    img_storage.validate_image(f, img_storage.MAX_PROFILE_IMAGE_BYTES)
    assert f.stream.tell() == 0


# ── validate_image: rejections ──

def test_validate_rejects_missing_file():
    with pytest.raises(img_storage.ImageValidationError):
        img_storage.validate_image(None, img_storage.MAX_PROFILE_IMAGE_BYTES)


def test_validate_rejects_disallowed_extension():
    buf = io.BytesIO(b"whatever")
    f = FileStorage(stream=buf, filename="malware.exe", content_type="application/octet-stream")
    with pytest.raises(img_storage.ImageValidationError):
        img_storage.validate_image(f, img_storage.MAX_PROFILE_IMAGE_BYTES)


def test_validate_rejects_svg_disguised_with_image_extension():
    # SVG can carry embedded <script> — never accepted even with an
    # image-looking name, and it also fails the Pillow raster decode below.
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    buf = io.BytesIO(svg)
    f = FileStorage(stream=buf, filename="pic.svg", content_type="image/svg+xml")
    with pytest.raises(img_storage.ImageValidationError):
        img_storage.validate_image(f, img_storage.MAX_PROFILE_IMAGE_BYTES)


def test_validate_rejects_html_content_with_faked_extension():
    # Wrong extension/MIME lie: a .jpg that is actually HTML.
    html = b"<html><body><script>alert(1)</script></body></html>"
    buf = io.BytesIO(html)
    f = FileStorage(stream=buf, filename="innocent.jpg", content_type="image/jpeg")
    with pytest.raises(img_storage.ImageValidationError):
        img_storage.validate_image(f, img_storage.MAX_PROFILE_IMAGE_BYTES)


def test_validate_rejects_gif():
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, format="GIF")
    buf.seek(0)
    f = FileStorage(stream=buf, filename="anim.gif", content_type="image/gif")
    with pytest.raises(img_storage.ImageValidationError):
        img_storage.validate_image(f, img_storage.MAX_PROFILE_IMAGE_BYTES)


def test_validate_rejects_empty_file():
    buf = io.BytesIO(b"")
    f = FileStorage(stream=buf, filename="empty.jpg", content_type="image/jpeg")
    with pytest.raises(img_storage.ImageValidationError):
        img_storage.validate_image(f, img_storage.MAX_PROFILE_IMAGE_BYTES)


def test_validate_rejects_corrupt_png_without_500ing():
    # Regression: a real PNG with the right signature/extension/MIME type
    # but a corrupted IDAT chunk (bad CRC) made Pillow's verify() raise a
    # bare SyntaxError, which escaped the original narrower except clause
    # and 500'd the request instead of producing a clean rejection.
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, format="PNG")
    corrupt = bytearray(buf.getvalue())
    idat = corrupt.find(b"IDAT")
    assert idat != -1
    corrupt[idat + 8] ^= 0xFF  # flip a byte inside the IDAT chunk's data
    f = FileStorage(stream=io.BytesIO(bytes(corrupt)), filename="broken.png", content_type="image/png")
    with pytest.raises(img_storage.ImageValidationError):
        img_storage.validate_image(f, img_storage.MAX_PROFILE_IMAGE_BYTES)


def test_validate_rejects_oversized_upload():
    f = _make_image_file(fmt="JPEG", size=(50, 50))
    with pytest.raises(img_storage.ImageValidationError):
        img_storage.validate_image(f, max_bytes=10)  # absurdly small ceiling


def test_validate_rejects_mismatched_mimetype():
    f = _make_image_file(fmt="JPEG", mimetype="application/pdf")
    with pytest.raises(img_storage.ImageValidationError):
        img_storage.validate_image(f, img_storage.MAX_PROFILE_IMAGE_BYTES)


# ── _upload / upload_profile_image / upload_cover_image: fully mocked ──

def test_upload_profile_image_raises_when_not_configured(monkeypatch):
    monkeypatch.delenv("CLOUDINARY_URL", raising=False)
    f = _make_image_file()
    with pytest.raises(img_storage.ImageStorageError):
        img_storage.upload_profile_image(1, f)


def test_upload_profile_image_returns_secure_url_on_success(monkeypatch):
    monkeypatch.setenv("CLOUDINARY_URL", "cloudinary://key:secret@demo")
    calls = {}

    def fake_upload(file_storage, **kwargs):
        calls.update(kwargs)
        return {"secure_url": "https://res.cloudinary.com/demo/image/upload/v1/waveline/users/1/profile.jpg"}

    monkeypatch.setattr(img_storage.cloudinary.uploader, "upload", fake_upload)
    f = _make_image_file()
    url = img_storage.upload_profile_image(1, f)
    assert url == "https://res.cloudinary.com/demo/image/upload/v1/waveline/users/1/profile.jpg"
    assert calls["public_id"] == "waveline/users/1/profile"
    assert calls["overwrite"] is True


def test_upload_cover_image_uses_distinct_public_id(monkeypatch):
    monkeypatch.setenv("CLOUDINARY_URL", "cloudinary://key:secret@demo")
    calls = {}

    def fake_upload(file_storage, **kwargs):
        calls.update(kwargs)
        return {"secure_url": "https://res.cloudinary.com/demo/image/upload/v1/waveline/users/1/cover.jpg"}

    monkeypatch.setattr(img_storage.cloudinary.uploader, "upload", fake_upload)
    f = _make_image_file()
    img_storage.upload_cover_image(1, f)
    assert calls["public_id"] == "waveline/users/1/cover"


def test_upload_failure_raises_safe_error_without_leaking_details(monkeypatch):
    monkeypatch.setenv("CLOUDINARY_URL", "cloudinary://key:secret@demo")

    def boom(file_storage, **kwargs):
        raise RuntimeError("cloudinary://realkey:realsecret@demo rejected the request")

    monkeypatch.setattr(img_storage.cloudinary.uploader, "upload", boom)
    f = _make_image_file()
    with pytest.raises(img_storage.ImageStorageError) as exc_info:
        img_storage.upload_profile_image(1, f)
    # The safe, generic message only — never the raw exception/credentials.
    assert "realkey" not in str(exc_info.value)
    assert "realsecret" not in str(exc_info.value)


def test_upload_missing_secure_url_raises_safe_error(monkeypatch):
    monkeypatch.setenv("CLOUDINARY_URL", "cloudinary://key:secret@demo")
    monkeypatch.setattr(img_storage.cloudinary.uploader, "upload", lambda f, **k: {})
    f = _make_image_file()
    with pytest.raises(img_storage.ImageStorageError):
        img_storage.upload_profile_image(1, f)

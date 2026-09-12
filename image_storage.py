"""Cloudinary-backed image storage for user profile/cover uploads.

Isolates every Cloudinary-specific detail behind a small interface so
``profiles.py`` never talks to the SDK directly, and so tests can mock this
whole module instead of making real network calls.

No image bytes are ever stored in PostgreSQL or on the local/Railway
filesystem — only the resulting Cloudinary ``secure_url`` is persisted, on
the ``users`` row.

Configuration is read lazily, inside functions, rather than cached at import
time. ``dashboard.py`` imports every blueprint (and everything they import)
*before* calling ``load_dotenv()``, so a module-level ``os.getenv(...)`` read
at import time would see a stale/empty environment in local development.
Reading ``CLOUDINARY_URL`` at call time and configuring the SDK explicitly
sidesteps that ordering issue.
"""

import logging
import os

import cloudinary
import cloudinary.uploader
from PIL import Image

logger = logging.getLogger(__name__)

MAX_PROFILE_IMAGE_BYTES = 5 * 1024 * 1024   # 5 MB
MAX_COVER_IMAGE_BYTES = 10 * 1024 * 1024    # 10 MB

_ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
_ALLOWED_MIMETYPES = {"image/jpeg", "image/png", "image/webp"}
_ALLOWED_PIL_FORMATS = {"JPEG", "PNG", "WEBP"}

# Upload-time transformations: only ever downscale ("limit" crop mode never
# upscales or crops content away), so a user's original composition is
# preserved — we just cap how large the stored asset can be.
_PROFILE_TRANSFORM = [{"width": 1200, "height": 1200, "crop": "limit"}]
_COVER_TRANSFORM = [{"width": 2400, "height": 900, "crop": "limit"}]


class ImageValidationError(ValueError):
    """The uploaded file itself is unusable. Message is safe to show as-is."""


class ImageStorageError(RuntimeError):
    """Storage isn't configured, or the upload failed. Message is safe to show as-is."""


def is_image_storage_configured():
    return bool(os.getenv("CLOUDINARY_URL"))


def _configure():
    if not os.getenv("CLOUDINARY_URL"):
        raise ImageStorageError("Image uploads aren't configured on this server yet.")

    # cloudinary.config(cloudinary_url=...) does NOT parse the URL into
    # cloud_name/api_key/api_secret — it only sets `cloudinary_url` as an
    # inert extra attribute on the existing Config object (see
    # cloudinary.BaseConfig.update(), which just does
    # self.__dict__[k] = v for each keyword). URL parsing only happens
    # inside Config.__init__() -> _load_config_from_env(), which reads
    # CLOUDINARY_URL from the environment. reset_config() is the supported,
    # public way to force that re-parse against whatever CLOUDINARY_URL is
    # present *right now* — necessary here because dashboard.py imports
    # every blueprint (pulling in this module, and so `import cloudinary`,
    # which creates the module-level Config() once) before load_dotenv()
    # runs, so the very first Config() can easily be built before .env has
    # populated os.environ.
    cloudinary.reset_config()
    cloudinary.config(secure=True)


def _extension_of(filename):
    if not filename or "." not in filename:
        return ""
    return filename.rsplit(".", 1)[1].lower()


def validate_image(file_storage, max_bytes):
    """Raise ImageValidationError if `file_storage` isn't an acceptable
    image. The filename and reported Content-Type are both attacker
    controlled, so neither is trusted alone — the decisive check is Pillow
    actually decoding the bytes and confirming the real format. Leaves the
    underlying stream positioned at 0 on return, ready for upload."""
    if file_storage is None or not file_storage.filename:
        raise ImageValidationError("Choose an image to upload.")

    if _extension_of(file_storage.filename) not in _ALLOWED_EXTENSIONS:
        raise ImageValidationError("Please upload a JPG, PNG or WebP image.")

    if file_storage.mimetype and file_storage.mimetype not in _ALLOWED_MIMETYPES:
        raise ImageValidationError("Please upload a JPG, PNG or WebP image.")

    stream = file_storage.stream
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    if size == 0:
        raise ImageValidationError("That file looks empty. Choose a different image.")
    if size > max_bytes:
        raise ImageValidationError(f"That image is too large (max {max_bytes // (1024 * 1024)} MB).")

    try:
        with Image.open(stream) as img:
            img.verify()
            fmt = img.format
    except Exception:
        # Deliberately broad: Pillow can raise many different exception
        # types for malformed/corrupt image data depending on exactly how
        # it's broken (UnidentifiedImageError, OSError, ValueError,
        # SyntaxError, struct.error, ...) — every one of them means the
        # same thing here: this is not a usable image, reject it cleanly
        # rather than letting an unexpected exception type 500 the request.
        raise ImageValidationError("That file doesn't look like a valid image.")
    finally:
        stream.seek(0)

    if fmt not in _ALLOWED_PIL_FORMATS:
        raise ImageValidationError("Please upload a JPG, PNG or WebP image.")


def _upload(file_storage, public_id, transformation):
    _configure()
    try:
        result = cloudinary.uploader.upload(
            file_storage,
            public_id=public_id,
            overwrite=True,
            invalidate=True,
            resource_type="image",
            transformation=transformation,
        )
    except ImageStorageError:
        raise
    except Exception as e:
        # Never surface Cloudinary internals/credentials to the user.
        logger.warning("Cloudinary upload failed for %s: %s", public_id, e)
        raise ImageStorageError("We couldn't upload your image right now. Please try again.")

    secure_url = result.get("secure_url") if isinstance(result, dict) else None
    if not secure_url:
        logger.warning("Cloudinary upload for %s returned no secure_url", public_id)
        raise ImageStorageError("We couldn't upload your image right now. Please try again.")
    return secure_url


def upload_profile_image(user_id, file_storage):
    validate_image(file_storage, MAX_PROFILE_IMAGE_BYTES)
    return _upload(file_storage, f"waveline/users/{user_id}/profile", _PROFILE_TRANSFORM)


def upload_cover_image(user_id, file_storage):
    validate_image(file_storage, MAX_COVER_IMAGE_BYTES)
    return _upload(file_storage, f"waveline/users/{user_id}/cover", _COVER_TRANSFORM)

"""Social links on user profiles (27 Sep 2026).

Users add their Instagram, SoundCloud, X, Discord, Spotify, YouTube,
Bandcamp and TikTok. Each value is typed as a handle or a full URL; it is
validated per platform and stored as a clean https URL (Discord usernames,
which have no public profile URL, are stored as plain text). Links render as
one tidy row of labelled buttons on the profile, always with
rel="nofollow noopener noreferrer".
"""

import re
import logging
from urllib.parse import urlparse

from db import db_cursor

logger = logging.getLogger(__name__)

_HANDLE = re.compile(r"^@?([A-Za-z0-9._-]{1,40})$")

# key: (label, allowed hosts, how a bare handle becomes a URL, input hint)
PLATFORMS = {
    "instagram":  ("Instagram",  {"instagram.com"},                 "https://instagram.com/{}",   "instagram.com/"),
    "soundcloud": ("SoundCloud", {"soundcloud.com", "on.soundcloud.com"}, "https://soundcloud.com/{}", "soundcloud.com/"),
    "x":          ("X",          {"x.com", "twitter.com"},          "https://x.com/{}",           "x.com/"),
    "discord":    ("Discord",    {"discord.gg", "discord.com"},     None,                         "invite link or username"),
    "spotify":    ("Spotify",    {"open.spotify.com"},              None,                         "open.spotify.com/…"),
    "youtube":    ("YouTube",    {"youtube.com", "m.youtube.com"},  "https://youtube.com/@{}",    "youtube.com/@"),
    "bandcamp":   ("Bandcamp",   {"bandcamp.com"},                  "https://{}.bandcamp.com",    "name.bandcamp.com"),
    "tiktok":     ("TikTok",     {"tiktok.com"},                    "https://tiktok.com/@{}",     "tiktok.com/@"),
}

# Simple Icons slugs for the button icons.
ICON_SLUGS = {"instagram": "instagram", "soundcloud": "soundcloud", "x": "x",
              "discord": "discord", "spotify": "spotify", "youtube": "youtube",
              "bandcamp": "bandcamp", "tiktok": "tiktok"}

SCHEMA = """
    CREATE TABLE IF NOT EXISTS user_social_links (
        user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        platform VARCHAR(20) NOT NULL,
        value    VARCHAR(255) NOT NULL,
        PRIMARY KEY (user_id, platform)
    )
"""


class LinkError(ValueError):
    pass


def _host_ok(host, allowed):
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return any(host == h or host.endswith("." + h) for h in allowed)


def normalize(platform, raw):
    """Clean URL (or Discord username) for this platform; "" clears it.
    Raises LinkError with a user-facing message when the value is invalid."""
    label, allowed, template, _ = PLATFORMS[platform]
    value = (raw or "").strip()
    if not value:
        return ""
    if len(value) > 255:
        raise LinkError(f"{label}: that's too long.")

    lowered = value.lower()
    looks_like_url = ("://" in value or "/" in value
                      or any(lowered.startswith(h) or lowered.startswith("www." + h) for h in allowed))
    if looks_like_url:
        url = value if "://" in value else "https://" + value
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise LinkError(f"{label}: enter a link or a username.")
        if not _host_ok(parsed.netloc.split(":")[0], allowed):
            raise LinkError(f"{label}: that isn't a link to {label}.")
        path = parsed.path.rstrip("/")
        if platform == "bandcamp":
            path = path or ""
        elif not path:
            raise LinkError(f"{label}: add your profile, not just the site.")
        return f"https://{parsed.netloc.lower()}{path}"

    m = _HANDLE.match(value)
    if not m:
        raise LinkError(f"{label}: use letters, numbers, dots, dashes or underscores.")
    handle = m.group(1)
    if platform == "discord":
        return handle                      # shown as text; no public profile URL
    if template is None:
        raise LinkError(f"{label}: paste the full link to your profile.")
    if platform == "bandcamp" and not re.match(r"^[a-z0-9-]{1,40}$", handle.lower()):
        raise LinkError("Bandcamp: use the name in name.bandcamp.com.")
    return template.format(handle.lower() if platform == "bandcamp" else handle)


def display(platform, value):
    """(href or None, short text) for rendering one saved link."""
    if platform == "discord" and not value.startswith("https://"):
        return None, value
    parsed = urlparse(value)
    short = (parsed.netloc.replace("www.", "") + parsed.path).rstrip("/")
    return value, short


def ensure_schema():
    try:
        with db_cursor(commit=True) as cur:
            cur.execute(SCHEMA)
    except Exception as e:
        logger.error("user_social_links schema failed: %s", type(e).__name__)


def get_links(user_id):
    """{platform: value} in PLATFORMS order. {} on any error (never raises)."""
    try:
        with db_cursor() as cur:
            cur.execute("SELECT platform, value FROM user_social_links WHERE user_id = %s", (user_id,))
            rows = dict(cur.fetchall())
    except Exception:
        return {}
    return {p: rows[p] for p in PLATFORMS if rows.get(p)}


def save_links(user_id, cleaned):
    """Replace this user's links with `cleaned` ({platform: value}, "" = remove)."""
    with db_cursor(commit=True) as cur:
        for platform in PLATFORMS:
            value = cleaned.get(platform, "")
            if value:
                cur.execute("""
                    INSERT INTO user_social_links (user_id, platform, value) VALUES (%s, %s, %s)
                    ON CONFLICT (user_id, platform) DO UPDATE SET value = EXCLUDED.value
                """, (user_id, platform, value))
            else:
                cur.execute("DELETE FROM user_social_links WHERE user_id = %s AND platform = %s",
                            (user_id, platform))


def links_for_display(user_id, website=""):
    """Ordered list of {platform, label, href, text, icon} for the profile row."""
    out = []
    for platform, value in get_links(user_id).items():
        href, text = display(platform, value)
        out.append({"platform": platform, "label": PLATFORMS[platform][0],
                    "href": href, "text": text, "icon": ICON_SLUGS[platform]})
    if website:
        href = website if website.startswith(("http://", "https://")) else "https://" + website
        text = href.replace("https://", "").replace("http://", "").rstrip("/")
        # A "website" that is really a platform profile (e.g. a Spotify
        # artist link) shows as that platform's button, unless the user has
        # already added that platform separately.
        host = urlparse(href).netloc.split(":")[0]
        taken = {l["platform"] for l in out}
        match = next((p for p, meta in PLATFORMS.items() if _host_ok(host, meta[1])), None)
        if match and match not in taken:
            out.append({"platform": match, "label": PLATFORMS[match][0], "href": href,
                        "text": text, "icon": ICON_SLUGS[match]})
        elif not match:
            out.append({"platform": "website", "label": "Website", "href": href, "text": text, "icon": None})
    return out

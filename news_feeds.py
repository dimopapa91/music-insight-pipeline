"""Music news from independent, scene-focused publications (27 Sep 2026).

Replaces the mainstream mix (Pitchfork, NME, The Guardian, plus a Deezer
top-albums chart) with writers who cover the scenes the charts miss, grouped
by scene so people can browse what's happening in jazz, ambient, club music,
underground hip-hop and so on.

Every feed below was checked live on 27 Sep 2026 (valid RSS, recent posts,
images in the feed). Feeds are fetched in parallel, parsed defensively, and
the whole result is cached for an hour; a feed that fails keeps its last
good items so one outage never empties a scene.
"""

import html
import re
import time
import logging
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests as http_requests

logger = logging.getLogger(__name__)

SCENES = {
    "electronic":   "Electronic & club",
    "experimental": "Experimental & left-field",
    "ambient":      "Ambient & modern classical",
    "jazz":         "Jazz",
    "hiphop":       "Underground hip-hop",
    "psych":        "Psych, folk & rarities",
    "underground":  "Underground & global",
    "indie":        "Indie, heavy & beyond",
}

FEEDS = [
    {"name": "Bandcamp Daily",     "home": "https://daily.bandcamp.com",   "url": "https://daily.bandcamp.com/feed",       "scene": "underground"},
    {"name": "The Quietus",        "home": "https://thequietus.com",       "url": "https://thequietus.com/feed/",          "scene": "experimental"},
    {"name": "Aquarium Drunkard",  "home": "https://aquariumdrunkard.com", "url": "https://aquariumdrunkard.com/feed/",    "scene": "psych"},
    {"name": "Resident Advisor",   "home": "https://ra.co",                "url": "https://ra.co/xml/news.xml",            "scene": "electronic"},
    {"name": "XLR8R",              "home": "https://xlr8r.com",            "url": "https://xlr8r.com/feed/",               "scene": "electronic"},
    {"name": "FACT",               "home": "https://www.factmag.com",      "url": "https://www.factmag.com/feed/",         "scene": "electronic"},
    {"name": "Crack",              "home": "https://crackmagazine.net",    "url": "https://crackmagazine.net/feed/",       "scene": "electronic"},
    {"name": "A Closer Listen",    "home": "https://acloserlisten.com",    "url": "https://acloserlisten.com/feed/",       "scene": "ambient"},
    {"name": "Headphone Commute",  "home": "https://headphonecommute.com", "url": "https://headphonecommute.com/feed/",    "scene": "ambient"},
    {"name": "UK Jazz News",       "home": "https://ukjazznews.com",       "url": "https://ukjazznews.com/feed/",          "scene": "jazz"},
    {"name": "POW MAG",            "home": "https://www.powmag.net",       "url": "https://powmag.substack.com/feed",      "scene": "hiphop"},
    {"name": "Treble",             "home": "https://www.treblezine.com",   "url": "https://www.treblezine.com/feed/",      "scene": "indie"},
]

PER_FEED = 5          # newest items kept per publication, so no one site floods the page
CACHE_TTL = 3600
_TIMEOUT = 8
_UA = "WavelineNews/1.0 (+https://wearewaveline.com)"

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}
_IMG_RE = re.compile(r"<img[^>]+?\ssrc=[\"']([^\"']+)[\"']", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_BOILERPLATE_RE = re.compile(r"The post .*? (?:first )?appeared (?:first )?on .*?\.?$", re.I | re.S)

_cache = {"data": None, "at": 0.0}
_last_good = {}   # feed name -> items


def _text(el, path):
    found = el.find(path, _NS)
    return (found.text or "").strip() if found is not None and found.text else ""


def _parse_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)   # naive UTC, like the rest of the app
    return dt


def _excerpt(raw, limit=180):
    text = html.unescape(_TAG_RE.sub(" ", raw or ""))
    text = _BOILERPLATE_RE.sub("", " ".join(text.split())).strip()
    text = re.sub(r"\s*(?:\[?…\]?|\[&#8230;\]|\.\.\.)\s*(?:Continued|Read more)?\s*$", "", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text


def _image(item, body):
    for tag in ("media:content", "media:thumbnail"):
        for el in item.findall(tag, _NS):
            url = el.get("url", "")
            medium = el.get("medium", "image")
            if url.startswith("https://") and medium in ("image", ""):
                return url
    enc = item.find("enclosure")
    if enc is not None and (enc.get("type") or "").startswith("image/") and enc.get("url", "").startswith("https://"):
        return enc.get("url")
    m = _IMG_RE.search(body or "")
    if m and m.group(1).startswith("https://"):
        return html.unescape(m.group(1))
    return ""


def parse_feed(content, feed):
    """Items from RSS 2.0 or Atom bytes. Never raises."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []
    channel = root.find("channel")
    entries = channel.findall("item") if channel is not None else root.findall("atom:entry", _NS)
    items = []
    for entry in entries:
        title = html.unescape(_text(entry, "title") or _text(entry, "atom:title"))
        link = _text(entry, "link")
        if not link:
            link_el = entry.find("atom:link[@rel='alternate']", _NS)
            if link_el is None:
                link_el = entry.find("atom:link", _NS)
            link = link_el.get("href", "") if link_el is not None else ""
        if not title or not link.startswith(("https://", "http://")):
            continue
        body = _text(entry, "content:encoded") or _text(entry, "description") \
            or _text(entry, "atom:summary") or _text(entry, "atom:content")
        published = _parse_date(_text(entry, "pubDate") or _text(entry, "atom:published")
                                or _text(entry, "atom:updated"))
        items.append({
            "title": _TAG_RE.sub("", title).strip(),
            "link": link.strip(),
            "excerpt": _excerpt(_text(entry, "description") or body),
            "image": _image(entry, body),
            "published": published,
            "source": feed["name"],
            "source_home": feed["home"],
            "scene": feed["scene"],
            "scene_label": SCENES[feed["scene"]],
        })
        if len(items) >= PER_FEED:
            break
    return items


def fetch_feed(feed):
    try:
        resp = http_requests.get(feed["url"], timeout=_TIMEOUT, headers={"User-Agent": _UA})
        if resp.status_code != 200:
            raise ValueError(f"HTTP {resp.status_code}")
        items = parse_feed(resp.content, feed)
        if items:
            _last_good[feed["name"]] = items
            return items
    except Exception as e:
        logger.warning("News feed %s failed: %s", feed["name"], type(e).__name__)
    return _last_good.get(feed["name"], [])


def get_news():
    """{"articles": [...newest first], "scenes": {key: label}, "sources": [...],
    "counts": {scene: n}}. Cached for an hour."""
    if _cache["data"] is not None and time.time() - _cache["at"] < CACHE_TTL:
        return _cache["data"]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(fetch_feed, FEEDS))
    articles = [a for items in results for a in items]
    epoch = datetime(1970, 1, 1)
    articles.sort(key=lambda a: a["published"] or epoch, reverse=True)
    counts = {}
    for a in articles:
        counts[a["scene"]] = counts.get(a["scene"], 0) + 1
    data = {
        "articles": articles,
        "scenes": SCENES,
        "counts": counts,
        "sources": [{"name": f["name"], "home": f["home"], "scene": SCENES[f["scene"]]} for f in FEEDS],
    }
    _cache["data"], _cache["at"] = data, time.time()
    return data


def clear_cache():
    _cache["data"], _cache["at"] = None, 0.0

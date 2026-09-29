"""Site-level plumbing added after the 27 Sep 2026 audit.

* Security headers on every response.
* Long-lived caching for static assets, safe because base.html links them
  with a content-hash ``?v=`` (see ``static_url``).
* Same-origin check on state-changing requests (lightweight CSRF defence on
  top of SameSite=Lax cookies).
* Discovery files: robots.txt, sitemap.xml, favicon.ico, llms.txt,
  /.well-known/x402 (x402scan format) and /openapi.json (x-payment-info).
"""

import hashlib
import json
import os
import time
from urllib.parse import quote, urlparse

from flask import Blueprint, Response, abort, current_app, redirect, request, url_for

from db import db_cursor

site_meta_bp = Blueprint("site_meta", __name__)

SITE_URL = os.getenv("SITE_URL", "https://wearewaveline.com").rstrip("/")

# ── security headers ────────────────────────────────────────────────
#
# CSP: scripts are self-hosted plus a few inline blocks (theme boot, window.WV,
# speculation rules), so 'unsafe-inline' is still needed for scripts; the win
# here is locking down framing, plugins, base-uri and form targets.
# Images come from Deezer/Spotify/Cloudinary/Last.fm CDNs, so img-src allows
# https: broadly. Audio previews come from Deezer's CDN. Fonts are Google
# Fonts (stylesheet on fonts.googleapis.com, files on fonts.gstatic.com).
_CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' https://api.deezer.com",   # photo-fallback.js (JSONP)
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "img-src 'self' data: blob: https:",
    "media-src 'self' https:",
    "font-src 'self' data: https://fonts.gstatic.com",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
])

SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Content-Security-Policy": _CSP,
}

STATIC_MAX_AGE = 31536000  # 1 year; URLs carry a content hash


def add_security_headers(response):
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    if request.path.startswith("/static/"):
        if request.args.get("v"):
            response.headers["Cache-Control"] = f"public, max-age={STATIC_MAX_AGE}, immutable"
        else:
            response.headers["Cache-Control"] = "public, max-age=3600"
    return response


# ── versioned static URLs ───────────────────────────────────────────

_hash_cache = {}


def static_url(filename):
    """url_for('static') plus ?v=<content hash>, cached per process."""
    h = _hash_cache.get(filename)
    if h is None:
        path = os.path.join(current_app.static_folder, filename)
        try:
            with open(path, "rb") as f:
                h = hashlib.sha256(f.read()).hexdigest()[:10]
        except OSError:
            h = ""
        _hash_cache[filename] = h
    url = url_for("static", filename=filename)
    return f"{url}?v={h}" if h else url


# ── same-origin check for state-changing requests ───────────────────

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def check_same_origin():
    """Reject cross-site POST/PUT/PATCH/DELETE from browsers.

    Browsers always send Origin on cross-origin POSTs (and Referer in most
    cases), so a mismatch means another site is submitting a form on the
    user's behalf. Requests with neither header (curl, server-to-server,
    old clients) pass: they can't carry the victim's cookies anyway.
    """
    if request.method in _SAFE_METHODS:
        return None
    source = request.headers.get("Origin") or request.headers.get("Referer")
    if not source or source == "null":
        return None
    host = urlparse(source).netloc
    if host and host != request.host:
        abort(403)
    return None


# ── robots / sitemap / favicon ──────────────────────────────────────

ROBOTS_TXT = f"""User-agent: *
Disallow: /compare
Disallow: /search
Disallow: /api/
Allow: /api/insight
Allow: /api/insight/preview
Disallow: /admin/
Disallow: /messages
Disallow: /settings
Disallow: /notifications
Disallow: /u/
Allow: /
Allow: /artist/

Sitemap: {SITE_URL}/sitemap.xml
"""

_STATIC_PAGES = ["/", "/genres", "/news", "/about", "/discover", "/feed"]
_sitemap_cache = {"xml": None, "at": 0.0}
_SITEMAP_TTL = 3600
_SITEMAP_MAX_ARTISTS = 20000


def _analysed_artists():
    with db_cursor() as cur:
        cur.execute("""
            SELECT artist_name, MAX(searched_at)
            FROM searches
            WHERE claude_insight IS NOT NULL AND claude_insight <> ''
            GROUP BY artist_name
            ORDER BY MAX(searched_at) DESC
            LIMIT %s
        """, (_SITEMAP_MAX_ARTISTS,))
        return cur.fetchall()


def build_sitemap(genre_slugs):
    urls = [f"<url><loc>{SITE_URL}{p}</loc></url>" for p in _STATIC_PAGES]
    urls += [f"<url><loc>{SITE_URL}/genre/{quote(s)}</loc></url>" for s in genre_slugs]
    try:
        rows = _analysed_artists()
    except Exception:
        rows = []
    seen = set()
    for name, last in rows:
        key = (name or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        lastmod = f"<lastmod>{last.date().isoformat()}</lastmod>" if hasattr(last, "date") else ""
        loc = f"{SITE_URL}/artist/{quote(name.strip(), safe='')}"
        urls.append(f"<url><loc>{_xml_escape(loc)}</loc>{lastmod}</url>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(urls) + "</urlset>")


def _xml_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@site_meta_bp.route("/robots.txt")
def robots_txt():
    return Response(ROBOTS_TXT, mimetype="text/plain")


@site_meta_bp.route("/sitemap.xml")
def sitemap_xml():
    now = time.time()
    if _sitemap_cache["xml"] is None or now - _sitemap_cache["at"] > _SITEMAP_TTL:
        from services import GENRES
        _sitemap_cache["xml"] = build_sitemap([g["slug"] for g in GENRES])
        _sitemap_cache["at"] = now
    return Response(_sitemap_cache["xml"], mimetype="application/xml")


@site_meta_bp.route("/favicon.ico")
def favicon_ico():
    return redirect(url_for("static", filename="favicon.svg"), code=301)


# ── agent discovery ─────────────────────────────────────────────────

def _price():
    return current_app.config.get("X402_PRICE", "$0.005")


def _network():
    return current_app.config.get("X402_NETWORK", "eip155:8453")


LLMS_TXT = """# Waveline

> Music discovery site with AI-written artist insights. People browse it for
> free; AI agents can buy one artist insight per request over x402
> ({price} USDC on Base, no account or API key).

Built by Dimos Papageorgiou. Agent API built in collaboration with nsgoods.

## Agent API
- [Paid insight]({site}/api/insight?artist=Radiohead): GET /api/insight?artist=<name>. Returns 402 with payment terms; pay and retry for the insight.
- [Paid insight, path form]({site}/api/insight/artist/Radiohead): GET /api/insight/artist/<name>, same price and rules.
- [Free preview]({site}/api/insight/preview): one real insight in the paid response's exact shape.
- Only a 200 is charged. Unknown artist: 404, not charged. Missing artist: 400 before any payment request.
- [x402 manifest]({site}/.well-known/x402) · [OpenAPI]({site}/openapi.json) · [Docs](https://github.com/dimopapa91/music-insight-pipeline/blob/main/docs/x402.md)

## MCP server
- {site}/mcp (Streamable HTTP, no sign-in, read-only, free). Tools: search_artists, get_artist, recently_analysed, get_insight_preview.

## Pages
- [Home]({site}/): search any artist.
- [Genres]({site}/genres): browse by sound.
- [About]({site}/about): how Waveline works.
"""


@site_meta_bp.route("/llms.txt")
def llms_txt():
    return Response(LLMS_TXT.format(site=SITE_URL, price=_price().lstrip("$")),
                    mimetype="text/plain")


def x402_manifest():
    return {
        "version": 1,
        "x402Version": 2,
        "name": "Waveline",
        "description": ("AI-written artist insights for AI agents. Only a 200 is charged; "
                        "unknown artists return 404 and are never charged."),
        "network": _network(),
        "asset": "USDC",
        "priceHint": _price(),
        "resources": [
            f"{SITE_URL}/api/insight",
            f"{SITE_URL}/api/insight/artist/{{name}}",
        ],
        "previewUrl": f"{SITE_URL}/api/insight/preview",
        "openapi": f"{SITE_URL}/openapi.json",
        "mcp": f"{SITE_URL}/mcp",
        "docs": "https://github.com/dimopapa91/music-insight-pipeline/blob/main/docs/x402.md",
        "contact": SITE_URL + "/about",
    }


@site_meta_bp.route("/.well-known/x402")
def well_known_x402():
    return Response(json.dumps(x402_manifest(), indent=2), mimetype="application/json")


def openapi_doc():
    payment = {
        "protocols": ["x402"],
        "price": _price(),
        "asset": "USDC",
        "network": _network(),
        "chargedOn": "HTTP 200 only",
    }
    insight_schema = {
        "type": "object",
        "required": ["artist", "insight", "generated_at", "source"],
        "properties": {
            "artist": {"type": "string"},
            "insight": {"type": "string"},
            "generated_at": {"type": "string", "format": "date-time"},
            "source": {"type": "string"},
        },
    }
    responses = {
        "200": {"description": "The insight (charged).",
                "content": {"application/json": {"schema": insight_schema}}},
        "402": {"description": "Payment required; terms in the PAYMENT-REQUIRED header and JSON body."},
        "404": {"description": "No insight for this artist. Not charged."},
    }
    return {
        "openapi": "3.1.0",
        "info": {"title": "Waveline Agent API", "version": "1.0.0",
                 "description": "Pay-per-request artist insights over x402."},
        "servers": [{"url": SITE_URL}],
        "tags": [{"name": "x402"}],
        "paths": {
            "/api/insight": {"get": {
                "tags": ["x402"], "operationId": "getInsight",
                "summary": "AI-written insight for one artist",
                "parameters": [{"name": "artist", "in": "query", "required": True,
                                "schema": {"type": "string", "maxLength": 200}}],
                "x-payment-info": payment,
                "responses": {**responses, "400": {"description": "Missing artist; no payment requested."}},
            }},
            "/api/insight/artist/{name}": {"get": {
                "tags": ["x402"], "operationId": "getInsightByPath",
                "summary": "Same as /api/insight, artist in the path",
                "parameters": [{"name": "name", "in": "path", "required": True,
                                "schema": {"type": "string", "maxLength": 200}}],
                "x-payment-info": payment,
                "responses": responses,
            }},
            "/api/insight/preview": {"get": {
                "operationId": "getInsightPreview",
                "summary": "Free sample in the paid response's shape",
                "responses": {"200": {"description": "Preview insight (free)."}},
            }},
        },
    }


@site_meta_bp.route("/openapi.json")
def openapi_json():
    return Response(json.dumps(openapi_doc(), indent=2), mimetype="application/json")

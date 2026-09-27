"""Waveline MCP server (27 Sep 2026).

A read-only Model Context Protocol endpoint so AI agents (Claude, Cursor and
any Streamable-HTTP MCP client) can use Waveline as tools:

    POST https://wearewaveline.com/mcp      (JSON-RPC 2.0, stateless)

Free tools only. No wallet, no payments handled here: the full AI insight
stays behind the paid x402 endpoint, and the tools say where to buy it.

Implements the parts of the spec a stateless tool server needs: initialize,
notifications/initialized, ping, tools/list, tools/call. Responses are plain
application/json (allowed by Streamable HTTP); GET returns 405 because this
server never opens an SSE stream.
"""

import json
import logging

from flask import Blueprint, Response, current_app, request

from db import db_cursor
from rate_limit import limiter

logger = logging.getLogger(__name__)

mcp_bp = Blueprint("mcp", __name__)

SITE_URL = "https://wearewaveline.com"
SERVER_INFO = {"name": "waveline", "title": "Waveline", "version": "1.0.0"}
SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = (
    "Waveline analyses music artists. Use search_artists to find analysed artists, "
    "get_artist to read one artist's public profile (top tracks, play counts, page URL), "
    "recently_analysed for what people are exploring now, and get_insight_preview for a "
    "free sample of the paid AI insight. Full insights cost 0.005 USDC on Base via x402 at "
    f"{SITE_URL}/api/insight?artist=<name>; unknown artists are never charged."
)

_MAX_QUERY = 80
_MAX_LIMIT = 20

TOOLS = [
    {
        "name": "search_artists",
        "title": "Search analysed artists",
        "description": "Find artists Waveline has already analysed whose name contains the query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": _MAX_QUERY},
                "limit": {"type": "integer", "minimum": 1, "maximum": _MAX_LIMIT, "default": 10},
            },
            "required": ["query"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "get_artist",
        "title": "Artist profile",
        "description": ("Public profile of one analysed artist: canonical name, top tracks with "
                        "play counts, when it was last analysed, its Waveline page, and where to "
                        "buy the full AI insight."),
        "inputSchema": {
            "type": "object",
            "properties": {"artist": {"type": "string", "minLength": 1, "maxLength": 200}},
            "required": ["artist"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "recently_analysed",
        "title": "Recently analysed",
        "description": "Artists most recently analysed on Waveline, newest first.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": _MAX_LIMIT, "default": 10}},
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "get_insight_preview",
        "title": "Free insight preview",
        "description": "One real AI insight in exactly the paid response's shape, free.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
]


# ── tool implementations ────────────────────────────────────────────

def _artist_url(name):
    from urllib.parse import quote
    return f"{SITE_URL}/artist/{quote(name, safe='')}"


def _limit(args, default=10):
    try:
        n = int(args.get("limit", default))
    except (TypeError, ValueError):
        n = default
    return max(1, min(_MAX_LIMIT, n))


def tool_search_artists(args):
    q = str(args.get("query", "")).strip()[:_MAX_QUERY]
    if not q:
        raise ValueError("query is required")
    escaped = q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    with db_cursor() as cur:
        cur.execute("""
            SELECT artist_name, MAX(searched_at) AS last
            FROM searches
            WHERE LOWER(artist_name) LIKE %s ESCAPE '\\'
              AND claude_insight IS NOT NULL AND claude_insight <> ''
            GROUP BY artist_name
            ORDER BY (LOWER(artist_name) = %s) DESC, MAX(searched_at) DESC
            LIMIT %s
        """, (f"%{escaped}%", q.lower(), _limit(args)))
        rows = cur.fetchall()
    return {"artists": [{"name": r[0], "url": _artist_url(r[0])} for r in rows]}


def tool_get_artist(args):
    name = str(args.get("artist", "")).strip()[:200]
    if not name:
        raise ValueError("artist is required")
    with db_cursor() as cur:
        cur.execute("""
            SELECT artist_name, searched_at, top_tracks
            FROM searches WHERE LOWER(artist_name) = LOWER(%s)
            ORDER BY searched_at DESC LIMIT 1
        """, (name,))
        row = cur.fetchone()
    if not row:
        return {"found": False, "artist": name,
                "message": "Not analysed on Waveline yet."}
    canonical, searched_at, tracks_raw = row
    tracks = tracks_raw if isinstance(tracks_raw, list) else json.loads(tracks_raw or "[]")
    top = []
    for t in tracks:
        try:
            top.append({"name": t["name"], "plays": int(t.get("playcount", 0) or 0)})
        except (KeyError, TypeError, ValueError):
            continue
    top.sort(key=lambda t: t["plays"], reverse=True)
    from urllib.parse import quote
    return {
        "found": True,
        "artist": canonical,
        "url": _artist_url(canonical),
        "last_analysed": searched_at.isoformat() if hasattr(searched_at, "isoformat") else str(searched_at),
        "top_tracks": top[:5],
        "full_insight": {
            "url": f"{SITE_URL}/api/insight?artist={quote(canonical)}",
            "price": current_app.config.get("X402_PRICE", "$0.005"),
            "protocol": "x402",
            "network": current_app.config.get("X402_NETWORK", "eip155:8453"),
        },
    }


def tool_recently_analysed(args):
    with db_cursor() as cur:
        cur.execute("""
            SELECT artist_name, MAX(searched_at) AS last
            FROM searches
            WHERE claude_insight IS NOT NULL AND claude_insight <> ''
            GROUP BY LOWER(artist_name), artist_name
            ORDER BY last DESC
            LIMIT %s
        """, (_limit(args),))
        rows = cur.fetchall()
    seen, out = set(), []
    for name, last in rows:
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append({"name": name, "url": _artist_url(name),
                    "analysed_at": last.isoformat() if hasattr(last, "isoformat") else str(last)})
    return {"artists": out}


def tool_get_insight_preview(args):
    import agent_api
    row = agent_api._latest_insight(agent_api.PREVIEW_ARTIST)
    if row is None:
        return {"available": False}
    body = agent_api._insight_body(row)
    body.update({"preview": True,
                 "paid_endpoint": f"{SITE_URL}/api/insight?artist=<name>",
                 "price": current_app.config.get("X402_PRICE", "$0.005")})
    return body


HANDLERS = {
    "search_artists": tool_search_artists,
    "get_artist": tool_get_artist,
    "recently_analysed": tool_recently_analysed,
    "get_insight_preview": tool_get_insight_preview,
}


# ── JSON-RPC plumbing ───────────────────────────────────────────────

def _result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle_message(msg):
    """One JSON-RPC message in, one response dict out (None for notifications)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
    method, msg_id = msg["method"], msg.get("id")
    params = msg.get("params") or {}
    is_notification = "id" not in msg

    if method == "initialize":
        asked = params.get("protocolVersion")
        version = asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        return _result(msg_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS,
        })
    if method.startswith("notifications/"):
        return None
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        handler = HANDLERS.get(name)
        if handler is None:
            return _error(msg_id, -32602, f"Unknown tool: {name}")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _error(msg_id, -32602, "arguments must be an object")
        try:
            data = handler(args)
            return _result(msg_id, {
                "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
                "structuredContent": data,
                "isError": False,
            })
        except ValueError as e:
            return _result(msg_id, {"content": [{"type": "text", "text": str(e)}], "isError": True})
        except Exception:
            logger.exception("MCP tool %s failed", name)
            return _result(msg_id, {"content": [{"type": "text", "text": "Temporarily unavailable."}],
                                    "isError": True})
    if is_notification:
        return None
    return _error(msg_id, -32601, f"Method not found: {method}")


@mcp_bp.route("/mcp", methods=["POST"])
@limiter.limit("120 per minute")
def mcp_post():
    try:
        payload = json.loads(request.get_data(as_text=True) or "null")
    except ValueError:
        return Response(json.dumps(_error(None, -32700, "Parse error")), status=400,
                        mimetype="application/json")
    if isinstance(payload, list):
        responses = [r for r in (handle_message(m) for m in payload) if r is not None]
        if not responses:
            return Response(status=202)
        return Response(json.dumps(responses), mimetype="application/json")
    response = handle_message(payload)
    if response is None:
        return Response(status=202)
    return Response(json.dumps(response), mimetype="application/json")


@mcp_bp.route("/mcp", methods=["GET", "DELETE"])
def mcp_no_stream():
    return Response(status=405, headers={"Allow": "POST"})

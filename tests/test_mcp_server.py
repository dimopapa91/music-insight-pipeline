"""Waveline MCP server (27 Sep 2026): JSON-RPC over Streamable HTTP, free
read-only tools. No network or real database."""

import contextlib
import datetime
import json

import dashboard
import mcp_server


def _post(payload, **headers):
    return dashboard.app.test_client().post("/mcp", data=json.dumps(payload),
                                             content_type="application/json", headers=headers)


def _rpc(method, params=None, msg_id=1):
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    return _post(body).get_json()


def _fake_db(monkeypatch, rows=None, one=None, capture=None):
    class Cur:
        def execute(self, sql, params=None):
            if capture is not None:
                capture.append((sql, params))

        def fetchall(self):
            return rows or []

        def fetchone(self):
            return one

    @contextlib.contextmanager
    def cm(commit=False):
        yield Cur()
    monkeypatch.setattr(mcp_server, "db_cursor", cm)


def test_initialize_negotiates_version_and_advertises_tools():
    r = _rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                            "clientInfo": {"name": "t", "version": "1"}})
    res = r["result"]
    assert res["protocolVersion"] == "2025-06-18"
    assert res["capabilities"]["tools"] == {"listChanged": False}
    assert res["serverInfo"]["name"] == "waveline"
    assert "x402" in res["instructions"]
    unknown = _rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]
    assert unknown["protocolVersion"] == mcp_server.SUPPORTED_VERSIONS[0]


def test_notification_gets_202_and_ping_works():
    resp = _post({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert resp.status_code == 202
    assert _rpc("ping")["result"] == {}


def test_tools_list_is_read_only():
    tools = _rpc("tools/list")["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == {"search_artists", "get_artist", "recently_analysed", "get_insight_preview"}
    assert all(t["annotations"]["readOnlyHint"] for t in tools)
    assert all(t["inputSchema"]["type"] == "object" for t in tools)


def test_search_artists_escapes_like_and_returns_urls(monkeypatch):
    seen = []
    _fake_db(monkeypatch, rows=[("Massive Attack", datetime.datetime(2026, 9, 1))], capture=seen)
    r = _rpc("tools/call", {"name": "search_artists", "arguments": {"query": "50%_off", "limit": 99}})
    data = r["result"]["structuredContent"]
    assert data["artists"][0]["url"] == "https://wearewaveline.com/artist/Massive%20Attack"
    sql, params = seen[0]
    assert params[0] == "%50\\%\\_off%"
    assert params[2] == 20                        # limit capped
    assert json.loads(r["result"]["content"][0]["text"]) == data


def test_get_artist_found_hides_paid_insight(monkeypatch):
    tracks = json.dumps([{"name": "Angel", "playcount": "10"}, {"name": "Teardrop", "playcount": "99"}])
    _fake_db(monkeypatch, one=("Massive Attack", datetime.datetime(2026, 9, 1), tracks))
    data = _rpc("tools/call", {"name": "get_artist", "arguments": {"artist": "massive attack"}})["result"]["structuredContent"]
    assert data["found"] is True and data["artist"] == "Massive Attack"
    assert data["top_tracks"][0] == {"name": "Teardrop", "plays": 99}
    assert data["full_insight"]["protocol"] == "x402"
    assert "insight" not in data                  # the paid text is never given away


def test_get_artist_not_found(monkeypatch):
    _fake_db(monkeypatch, one=None)
    data = _rpc("tools/call", {"name": "get_artist", "arguments": {"artist": "Nobody"}})["result"]["structuredContent"]
    assert data["found"] is False


def test_bad_arguments_and_unknown_tool():
    r = _rpc("tools/call", {"name": "search_artists", "arguments": {"query": "  "}})
    assert r["result"]["isError"] is True
    r = _rpc("tools/call", {"name": "nope", "arguments": {}})
    assert r["error"]["code"] == -32602
    assert _rpc("does/not/exist")["error"]["code"] == -32601


def test_tool_failure_is_reported_not_raised(monkeypatch):
    @contextlib.contextmanager
    def boom(commit=False):
        raise RuntimeError("db down")
        yield
    monkeypatch.setattr(mcp_server, "db_cursor", boom)
    r = _rpc("tools/call", {"name": "recently_analysed", "arguments": {}})
    assert r["result"]["isError"] is True
    assert "db down" not in json.dumps(r)


def test_parse_error_batch_and_get():
    c = dashboard.app.test_client()
    assert c.post("/mcp", data="{not json", content_type="application/json").status_code == 400
    batch = c.post("/mcp", data=json.dumps([{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                            {"jsonrpc": "2.0", "method": "notifications/initialized"}]),
                   content_type="application/json").get_json()
    assert batch == [{"jsonrpc": "2.0", "id": 1, "result": {}}]
    assert c.get("/mcp").status_code == 405


def test_cross_site_browser_post_rejected():
    resp = _post({"jsonrpc": "2.0", "id": 1, "method": "ping"}, Origin="https://evil.example")
    assert resp.status_code == 403


def test_discovery_files_advertise_mcp():
    c = dashboard.app.test_client()
    assert "/mcp" in c.get("/llms.txt").data.decode()
    assert json.loads(c.get("/.well-known/x402").data)["mcp"] == "https://wearewaveline.com/mcp"

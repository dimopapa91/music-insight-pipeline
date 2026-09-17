"""Tests for email_digest.py's Resend HTTP API sending.

Railway blocks all outbound SMTP ports (25/465/587) below its Pro plan, so
a direct SMTP connection (even one forced over IPv4) times out in
production. send_digest() sends over HTTPS via the Resend API instead,
which is never blocked.

No real network call anywhere in this file — requests.post is mocked.
"""

import logging

import email_digest


def _fake_rows():
    return (
        [("Radiohead", "an insight", None, [{"name": "Karma Police", "playcount": 100}])],
        5,
    )


class _FakeResponse:
    def __init__(self, status_code=200, text='{"id":"abc123"}'):
        self.status_code = status_code
        self.text = text
        self.ok = 200 <= status_code < 400


def test_send_digest_posts_to_resend_with_bearer_auth_and_full_body(monkeypatch):
    monkeypatch.setenv("DIGEST_EMAIL_FROM", "onboarding@resend.dev")
    monkeypatch.setenv("DIGEST_EMAIL_TO", "to@example.com")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key_123")
    monkeypatch.setattr(email_digest, "get_weekly_data", lambda: _fake_rows())

    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return _FakeResponse(200)

    monkeypatch.setattr(email_digest.requests, "post", fake_post)

    email_digest.send_digest()

    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "https://api.resend.com/emails"
    assert call["headers"]["Authorization"] == "Bearer re_test_key_123"
    assert call["timeout"] == 30
    body = call["json"]
    assert body["from"] == "onboarding@resend.dev"
    assert body["to"] == ["to@example.com"]
    assert "Weekly Music Digest" in body["subject"]
    assert "Radiohead" in body["html"]
    assert "Radiohead" in body["text"]


def test_no_rows_this_week_skips_sending(monkeypatch):
    monkeypatch.setenv("DIGEST_EMAIL_FROM", "onboarding@resend.dev")
    monkeypatch.setenv("DIGEST_EMAIL_TO", "to@example.com")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key_123")
    monkeypatch.setattr(email_digest, "get_weekly_data", lambda: ([], 0))

    called = {"n": 0}
    monkeypatch.setattr(email_digest.requests, "post", lambda *a, **k: called.update(n=called["n"] + 1))

    email_digest.send_digest()
    assert called["n"] == 0


def test_missing_api_key_skips_sending(monkeypatch):
    monkeypatch.setenv("DIGEST_EMAIL_FROM", "onboarding@resend.dev")
    monkeypatch.setenv("DIGEST_EMAIL_TO", "to@example.com")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setattr(email_digest, "get_weekly_data", lambda: _fake_rows())

    called = {"n": 0}
    monkeypatch.setattr(email_digest.requests, "post", lambda *a, **k: called.update(n=called["n"] + 1))

    email_digest.send_digest()
    assert called["n"] == 0


def test_non_200_response_does_not_raise(monkeypatch):
    monkeypatch.setenv("DIGEST_EMAIL_FROM", "onboarding@resend.dev")
    monkeypatch.setenv("DIGEST_EMAIL_TO", "to@example.com")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key_123")
    monkeypatch.setattr(email_digest, "get_weekly_data", lambda: _fake_rows())
    monkeypatch.setattr(email_digest.requests, "post", lambda *a, **k: _FakeResponse(422, '{"message":"invalid from domain"}'))

    email_digest.send_digest()  # must not raise


def test_request_exception_does_not_raise(monkeypatch):
    monkeypatch.setenv("DIGEST_EMAIL_FROM", "onboarding@resend.dev")
    monkeypatch.setenv("DIGEST_EMAIL_TO", "to@example.com")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key_123")
    monkeypatch.setattr(email_digest, "get_weekly_data", lambda: _fake_rows())

    def _boom(*a, **k):
        raise ConnectionError("simulated network failure")

    monkeypatch.setattr(email_digest.requests, "post", _boom)

    email_digest.send_digest()  # must not raise


def test_api_key_never_reaches_the_logs_on_non_200_response(monkeypatch, caplog):
    monkeypatch.setenv("DIGEST_EMAIL_FROM", "onboarding@resend.dev")
    monkeypatch.setenv("DIGEST_EMAIL_TO", "to@example.com")
    monkeypatch.setenv("RESEND_API_KEY", "super-secret-resend-key")
    monkeypatch.setattr(email_digest, "get_weekly_data", lambda: _fake_rows())
    monkeypatch.setattr(email_digest.requests, "post", lambda *a, **k: _FakeResponse(401, '{"message":"invalid API key"}'))

    with caplog.at_level(logging.ERROR):
        email_digest.send_digest()

    assert "super-secret-resend-key" not in caplog.text


def test_api_key_never_reaches_the_logs_on_request_exception(monkeypatch, caplog):
    monkeypatch.setenv("DIGEST_EMAIL_FROM", "onboarding@resend.dev")
    monkeypatch.setenv("DIGEST_EMAIL_TO", "to@example.com")
    monkeypatch.setenv("RESEND_API_KEY", "super-secret-resend-key")
    monkeypatch.setattr(email_digest, "get_weekly_data", lambda: _fake_rows())

    def _boom(url, headers=None, json=None, timeout=None):
        # Mirrors a real requests exception potentially embedding request
        # details (e.g. headers) in its message.
        raise ConnectionError(f"failed request with headers={headers}")

    monkeypatch.setattr(email_digest.requests, "post", _boom)

    with caplog.at_level(logging.ERROR):
        email_digest.send_digest()

    assert "super-secret-resend-key" not in caplog.text
    assert "ConnectionError" in caplog.text

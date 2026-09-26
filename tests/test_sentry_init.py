"""Tests for Sentry initialisation in dashboard.py.

_init_sentry() must be a no-op unless a SENTRY_DSN is configured, so local
dev and the test suite never send events. sentry_sdk.init is patched in
both tests so Sentry is never really initialised.
"""

import dashboard


def test_init_sentry_noop_without_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    calls = []
    monkeypatch.setattr(dashboard.sentry_sdk, "init", lambda **kw: calls.append(kw))
    assert dashboard._init_sentry() is False
    assert calls == []


def test_init_sentry_initialises_with_dsn(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://examplePublicKey@o0.ingest.sentry.io/0")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abc123")
    calls = []
    monkeypatch.setattr(dashboard.sentry_sdk, "init", lambda **kw: calls.append(kw))
    assert dashboard._init_sentry() is True
    assert len(calls) == 1
    assert calls[0]["dsn"].endswith("/0")
    assert calls[0]["environment"] == "production"
    assert calls[0]["release"] == "abc123"

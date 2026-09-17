"""Tests for email_digest.py's IPv4-forced SMTP connection.

Railway (and other containerised environments) can resolve smtp.gmail.com
to an IPv6 address with no outbound IPv6 route, failing with "[Errno 101]
Network is unreachable". _IPv4SMTP_SSL._get_socket() works around that by
resolving IPv4 explicitly and wrapping the raw socket in TLS itself, while
still validating the certificate against the real hostname.

No real network call anywhere in this file — socket/ssl are mocked.
"""

import socket
import ssl

import email_digest


def test_get_socket_forces_af_inet_and_preserves_hostname_verification(monkeypatch):
    getaddrinfo_calls = []

    def fake_getaddrinfo(host, port, family, socktype):
        getaddrinfo_calls.append((host, port, family, socktype))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 465))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    raw_sock = object()
    create_connection_calls = []

    def fake_create_connection(sockaddr, timeout=None, source_address=None):
        create_connection_calls.append((sockaddr, timeout, source_address))
        return raw_sock

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)

    wrapped_sock = object()
    wrap_calls = []

    class FakeContext:
        def wrap_socket(self, sock, server_hostname=None):
            wrap_calls.append((sock, server_hostname))
            return wrapped_sock

    instance = email_digest._IPv4SMTP_SSL.__new__(email_digest._IPv4SMTP_SSL)
    instance.context = FakeContext()
    instance._host = "smtp.gmail.com"
    instance.source_address = None

    result = instance._get_socket("smtp.gmail.com", 465, 30)

    # AF_INET was explicitly requested, not left to chance.
    assert getaddrinfo_calls == [("smtp.gmail.com", 465, socket.AF_INET, socket.SOCK_STREAM)]
    # The raw connection was opened to the resolved IPv4 address, not the hostname.
    assert create_connection_calls[0][0] == ("93.184.216.34", 465)
    # TLS wraps that raw IPv4 socket, but server_hostname stays the real
    # hostname -- SNI and certificate hostname checks still validate against
    # "smtp.gmail.com", never the raw IP.
    assert wrap_calls == [(raw_sock, "smtp.gmail.com")]
    # The wrapped socket -- not the raw one -- is what SMTP_SSL ends up using
    # for every subsequent protocol read/write.
    assert result is wrapped_sock


def test_send_digest_connects_with_ipv4_forcing_class_and_verified_context(monkeypatch):
    monkeypatch.setenv("DIGEST_EMAIL_FROM", "from@example.com")
    monkeypatch.setenv("DIGEST_EMAIL_TO", "to@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-password")

    monkeypatch.setattr(email_digest, "get_weekly_data", lambda: (
        [("Radiohead", "an insight", None, [{"name": "Karma Police", "playcount": 100}])], 5,
    ))

    calls = {}

    class FakeServer:
        def __init__(self, host, port, context=None):
            calls["host"] = host
            calls["port"] = port
            calls["context"] = context

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def login(self, user, password):
            calls["login"] = (user, password)

        def sendmail(self, from_addr, to_addrs, message):
            calls["sendmail"] = (from_addr, to_addrs)

    monkeypatch.setattr(email_digest, "_IPv4SMTP_SSL", FakeServer)

    email_digest.send_digest()

    assert calls["host"] == "smtp.gmail.com"
    assert calls["port"] == 465
    # A real, fully-verifying context -- not smtplib's historically
    # permissive default -- so hostname/certificate checks stay enforced.
    assert isinstance(calls["context"], ssl.SSLContext)
    assert calls["context"].check_hostname is True
    assert calls["context"].verify_mode == ssl.CERT_REQUIRED
    assert calls["login"] == ("from@example.com", "app-password")
    assert calls["sendmail"][0] == "from@example.com"
    assert calls["sendmail"][1] == "to@example.com"


def test_send_digest_swallows_smtp_errors_without_raising(monkeypatch):
    monkeypatch.setenv("DIGEST_EMAIL_FROM", "from@example.com")
    monkeypatch.setenv("DIGEST_EMAIL_TO", "to@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-password")

    monkeypatch.setattr(email_digest, "get_weekly_data", lambda: (
        [("Radiohead", "an insight", None, [{"name": "Karma Police", "playcount": 100}])], 5,
    ))

    class ExplodingServer:
        def __init__(self, host, port, context=None):
            pass

        def __enter__(self):
            raise OSError("[Errno 101] Network is unreachable")

        def __exit__(self, *exc_info):
            return False

    monkeypatch.setattr(email_digest, "_IPv4SMTP_SSL", ExplodingServer)

    email_digest.send_digest()  # must not raise

"""Tests for the db_cursor context manager — the connection-leak safeguard."""

import pytest

import db


class FakeCursor:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeConn:
    def __init__(self):
        self.closed = False
        self.committed = False
        self.rolled_back = False
        self._cursor = FakeCursor()

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_db_cursor_commits_and_closes_on_success(monkeypatch):
    fake = FakeConn()
    monkeypatch.setattr(db, "get_db_connection", lambda: fake)
    with db.db_cursor(commit=True) as cur:
        assert cur is fake._cursor
    assert fake.committed is True
    assert fake.closed is True
    assert fake._cursor.closed is True


def test_db_cursor_rolls_back_and_closes_on_error(monkeypatch):
    fake = FakeConn()
    monkeypatch.setattr(db, "get_db_connection", lambda: fake)
    with pytest.raises(RuntimeError):
        with db.db_cursor() as cur:
            raise RuntimeError("boom")
    # Even though the body raised, the connection is cleaned up
    assert fake.rolled_back is True
    assert fake.closed is True


# ── connection pool (27 Sep 2026) ───────────────────────────────────────

class FakePool:
    def __init__(self, conns):
        self.conns = list(conns)
        self.returned = []

    def getconn(self):
        return self.conns.pop(0)

    def putconn(self, conn, close=False):
        self.returned.append((conn, close))


def _pooled(monkeypatch, pool):
    monkeypatch.setattr(db, "_pool_enabled", lambda: True)
    monkeypatch.setattr(db, "_get_pool", lambda: pool)


def test_pooled_read_returns_connection_without_open_transaction(monkeypatch):
    fake = FakeConn()
    pool = FakePool([fake])
    _pooled(monkeypatch, pool)
    with db.db_cursor():
        pass
    assert fake.rolled_back is True          # implicit read txn ended
    assert fake.closed is False              # kept for reuse
    assert pool.returned == [(fake, False)]


def test_pooled_write_commits_and_returns(monkeypatch):
    fake = FakeConn()
    pool = FakePool([fake])
    _pooled(monkeypatch, pool)
    with db.db_cursor(commit=True):
        pass
    assert fake.committed is True
    assert pool.returned == [(fake, False)]


def test_pooled_broken_connection_is_discarded(monkeypatch):
    import psycopg2
    fake = FakeConn()
    pool = FakePool([fake])
    _pooled(monkeypatch, pool)
    with pytest.raises(psycopg2.OperationalError):
        with db.db_cursor():
            raise psycopg2.OperationalError("server closed the connection")
    assert pool.returned == [(fake, True)]


def test_pool_skips_a_connection_that_is_already_closed(monkeypatch):
    dead, live = FakeConn(), FakeConn()
    dead.closed = True
    pool = FakePool([dead, live])
    _pooled(monkeypatch, pool)
    with db.db_cursor() as cur:
        assert cur is live._cursor
    assert (dead, True) in pool.returned and (live, False) in pool.returned


def test_pool_disabled_in_tests_and_without_database_url(monkeypatch):
    assert db._pool_enabled() is False           # conftest sets DB_POOL=0
    monkeypatch.setenv("DB_POOL", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert db._pool_enabled() is False

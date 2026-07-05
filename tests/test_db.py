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

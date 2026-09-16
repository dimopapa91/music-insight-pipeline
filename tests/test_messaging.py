"""Tests for Phase 6: private one-to-one messaging.

Two layers, matching the rest of this suite's conventions:
- data/security layer (messaging.py): a fake db_cursor that records executed
  SQL/params (and, where relevant, returns canned fetchall/fetchone results
  in call order), so the actual query shape, validation order, and
  mutual-follow enforcement can be asserted without ever touching a real
  database.
- route/template layer (views_messages.py + messages_inbox.html /
  messages_thread.html): the data functions are monkeypatched directly
  (same pattern as views_notifications/views_discover tests), so these
  tests only exercise routing, templates, auth and redirect safety.

No network or real database calls anywhere in this file.
"""

import contextlib
import datetime

import pytest

import dashboard
import analytics
import messaging
import models
import profiles
import views_messages
from models import User

OWNER = User(id=1, username="dimos", email="d@e.com", password_hash="x")
OTHER = User(id=2, username="alice", email="a@e.com", password_hash="x")


def _login(client, monkeypatch, user=OWNER):
    monkeypatch.setattr(User, "get", classmethod(lambda cls, i: user))
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)


def _sequential_db_cursor(monkeypatch, target, results):
    """`results` is a list of fetchall()-shaped return values, consumed one
    per execute() call in the order the code under test issues them.
    fetchone() just returns the first row of the same list (or None)."""
    calls = []
    remaining = list(results)

    class FakeCur:
        def execute(self, sql, params=None):
            calls.append((sql, params))

        def fetchall(self):
            return remaining.pop(0) if remaining else []

        def fetchone(self):
            rows = remaining.pop(0) if remaining else []
            return rows[0] if rows else None

    @contextlib.contextmanager
    def cm(commit=False):
        yield FakeCur()

    monkeypatch.setattr(target, "db_cursor", cm)
    return calls


class _FakeConnection:
    """Tracks commit()/rollback() the way a real psycopg2 connection would,
    so atomicity tests can assert on actual transaction outcomes rather than
    just on which SQL strings were built."""
    def __init__(self):
        self.committed = False
        self.rolled_back = False

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def _atomic_fake_db_cursor(monkeypatch, target, results, fail_on_call_index=None, fail_exc=None):
    """Mirrors db.py's real db_cursor(): one shared connection, commits only
    if the with-block exits without raising, rolls back (and re-raises) if
    it does. `fail_on_call_index` makes the Nth execute() call raise, to
    simulate a failing INSERT partway through a transaction.

    Returns (calls, conn, open_count) — open_count["n"] is how many times
    db_cursor() itself was entered, which is exactly the number of separate
    transactions the code under test used.
    """
    conn = _FakeConnection()
    calls = []
    remaining = list(results)
    open_count = {"n": 0}

    class FakeCur:
        def execute(self, sql, params=None):
            calls.append((sql, params))
            if fail_on_call_index is not None and len(calls) - 1 == fail_on_call_index:
                raise (fail_exc or RuntimeError("simulated DB failure"))

        def fetchall(self):
            return remaining.pop(0) if remaining else []

        def fetchone(self):
            rows = remaining.pop(0) if remaining else []
            return rows[0] if rows else None

    @contextlib.contextmanager
    def cm(commit=False):
        open_count["n"] += 1
        cur = FakeCur()
        try:
            yield cur
            if commit:
                conn.commit()
        except Exception:
            conn.rollback()
            raise

    monkeypatch.setattr(target, "db_cursor", cm)
    return calls, conn, open_count


# ── schema ────────────────────────────────────────────────────────────

def test_messaging_schema_is_idempotent_and_present():
    joined = "\n".join(models.SCHEMA)
    assert "CREATE TABLE IF NOT EXISTS conversations" in joined
    assert "CREATE TABLE IF NOT EXISTS direct_messages" in joined
    assert "UNIQUE (user1_id, user2_id)" in joined
    assert "CHECK (user1_id < user2_id)" in joined
    assert "CHECK (sender_id <> recipient_id)" in joined
    assert "char_length(body) <= 2000" in joined


def test_init_db_actually_creates_messaging_tables(monkeypatch):
    executed = []

    class FakeCur:
        def execute(self, sql, params=None):
            executed.append(sql)

    @contextlib.contextmanager
    def fake_cm(commit=False):
        yield FakeCur()

    monkeypatch.setattr(models, "db_cursor", fake_cm)
    models.init_db()
    joined = "\n".join(executed)
    assert "CREATE TABLE IF NOT EXISTS conversations" in joined
    assert "CREATE TABLE IF NOT EXISTS direct_messages" in joined
    assert "idx_dm_conversation_created" in joined
    assert "idx_dm_recipient_unread" in joined


# ── canonical pair ───────────────────────────────────────────────────

def test_canonical_pair_orders_smaller_id_first():
    assert messaging._canonical_pair(5, 2) == (2, 5)
    assert messaging._canonical_pair(2, 5) == (2, 5)
    assert messaging._canonical_pair(1, 1) == (1, 1)


# ── get_conversation_between: no duplicates, race-safe ─────────────

def test_get_conversation_between_finds_existing(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, messaging, [[(42,)]])
    conv_id = messaging.get_conversation_between(5, 2, create=False)
    assert conv_id == 42
    assert calls[0][1] == (2, 5)  # canonicalised before the lookup


def test_get_conversation_between_returns_none_without_create(monkeypatch):
    _sequential_db_cursor(monkeypatch, messaging, [[]])
    assert messaging.get_conversation_between(5, 2, create=False) is None


def test_get_conversation_between_creates_exactly_one_new_row(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, messaging, [
        [(7,)],      # single UPSERT ... RETURNING id
    ])
    conv_id = messaging.get_conversation_between(5, 2, create=True)
    assert conv_id == 7
    assert len(calls) == 1
    assert "INSERT INTO conversations" in calls[0][0]
    assert "ON CONFLICT" in calls[0][0]


def test_get_conversation_between_create_is_race_safe_via_single_upsert(monkeypatch):
    # Two near-simultaneous senders both attempting to create the same
    # canonical pair: INSERT ... ON CONFLICT (user1_id, user2_id) DO UPDATE
    # ... RETURNING id always returns exactly one row in a single statement
    # — whether this call created the conversation or another concurrent
    # transaction already had — so there's no separate fallback SELECT and
    # no window where two rows could ever be created for the same pair.
    calls = _sequential_db_cursor(monkeypatch, messaging, [[(9,)]])
    conv_id = messaging.get_conversation_between(5, 2, create=True)
    assert conv_id == 9
    assert len(calls) == 1
    sql = calls[0][0]
    assert "ON CONFLICT (user1_id, user2_id) DO UPDATE" in sql
    assert "RETURNING id" in sql


# ── send_direct_message: validation order + mutual-follow enforcement ─

def test_send_message_to_self_is_rejected_with_no_query_at_all(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, messaging, [[("should", "not", "be", "used")]])
    assert messaging.send_direct_message(1, 1, "hi") is None
    assert calls == []


def test_send_blank_message_is_rejected_with_no_query_at_all(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, messaging, [[("unused",)]])
    assert messaging.send_direct_message(1, 2, "    ") is None
    assert calls == []


def test_send_oversized_message_is_rejected_with_no_query_at_all(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, messaging, [[("unused",)]])
    assert messaging.send_direct_message(1, 2, "x" * 2001) is None
    assert calls == []


def test_send_exactly_2000_chars_is_accepted(monkeypatch):
    body = "x" * 2000
    calls = _sequential_db_cursor(monkeypatch, messaging, [
        [(True,)],   # mutual-follow check
        [(3,)],      # conversation upsert RETURNING id
        [(99,)],     # INSERT direct_messages RETURNING id
    ])
    msg_id = messaging.send_direct_message(1, 2, body)
    assert msg_id == 99
    assert calls[-1][1] == (3, 1, 2, body)


def test_mutual_follow_permits_sending(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, messaging, [
        [(True,)],
        [(3,)],      # conversation already exists
        [(55,)],     # message inserted
    ])
    assert messaging.send_direct_message(1, 2, "hello") == 55
    assert "INSERT INTO direct_messages" in calls[-1][0]


def test_one_way_follow_does_not_permit_sending(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, messaging, [[(False,)]])
    assert messaging.send_direct_message(1, 2, "hello") is None
    # only the mutual-follow check ran — no conversation/message query at all
    assert len(calls) == 1
    assert "direct_messages" not in calls[0][0]


def test_no_follow_at_all_does_not_permit_sending(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, messaging, [[(False,)]])
    assert messaging.send_direct_message(1, 2, "hello") is None
    assert len(calls) == 1


def test_service_layer_itself_enforces_mutual_follow_not_just_the_route():
    import inspect
    src = inspect.getsource(messaging.send_direct_message)
    assert "_can_users_message_with_cursor(cur, sender_id, recipient_id)" in src


def test_valid_message_stores_correct_sender_and_recipient(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, messaging, [
        [(True,)], [(3,)], [(100,)],
    ])
    messaging.send_direct_message(1, 2, "hi there")
    insert_sql, insert_params = calls[-1]
    assert insert_params == (3, 1, 2, "hi there")


def test_message_body_is_never_built_into_sql_text(monkeypatch):
    distinctive_body = "SECRET_BODY_MARKER_should_never_appear_in_sql_text"
    calls = _sequential_db_cursor(monkeypatch, messaging, [
        [(True,)], [(3,)], [(101,)],
    ])
    messaging.send_direct_message(1, 2, distinctive_body)
    for sql, params in calls:
        assert distinctive_body not in sql  # only ever a bound %s parameter


# ── atomicity: the whole send is exactly one transaction ────────────

def test_successful_send_uses_exactly_one_db_cursor_transaction(monkeypatch):
    calls, conn, open_count = _atomic_fake_db_cursor(monkeypatch, messaging, [
        [(True,)],   # mutual-follow check
        [(3,)],      # conversation upsert RETURNING id
        [(99,)],     # message INSERT RETURNING id
    ])
    msg_id = messaging.send_direct_message(1, 2, "hello")
    assert msg_id == 99
    assert open_count["n"] == 1  # exactly one db_cursor() context for the whole send
    assert conn.committed is True
    assert conn.rolled_back is False


def test_send_does_not_call_public_helpers_that_open_separate_transactions(monkeypatch):
    # If send_direct_message() called the public can_users_message() or
    # get_conversation_between() instead of the cursor-level helpers, each
    # would open its OWN db_cursor() — a second/third transaction. Make both
    # public functions blow up if called at all, and confirm the send still
    # succeeds using only the shared cursor passed to the private helpers.
    def _boom(*a, **k):
        raise AssertionError("send_direct_message must not call this public helper")

    monkeypatch.setattr(messaging, "can_users_message", _boom)
    monkeypatch.setattr(messaging, "get_conversation_between", _boom)
    _atomic_fake_db_cursor(monkeypatch, messaging, [
        [(True,)], [(3,)], [(99,)],
    ])
    assert messaging.send_direct_message(1, 2, "hello") == 99


def test_conversation_and_message_inserts_share_the_same_transaction(monkeypatch):
    calls, conn, open_count = _atomic_fake_db_cursor(monkeypatch, messaging, [
        [(True,)], [(3,)], [(99,)],
    ])
    messaging.send_direct_message(1, 2, "hello")
    assert open_count["n"] == 1
    sqls = [sql for sql, _ in calls]
    assert any("INSERT INTO conversations" in s for s in sqls)
    assert any("INSERT INTO direct_messages" in s for s in sqls)


def test_failed_message_insert_rolls_back_the_whole_send_including_conversation(monkeypatch):
    # The message INSERT (the 3rd execute() call: permission check,
    # conversation upsert, then this) raises. Nothing must be committed —
    # in particular, the just-created/just-touched conversation row must
    # not survive as an orphan when the message itself never made it in.
    calls, conn, open_count = _atomic_fake_db_cursor(
        monkeypatch, messaging,
        [[(True,)], [(3,)], []],
        fail_on_call_index=2, fail_exc=RuntimeError("simulated insert failure"),
    )
    with pytest.raises(RuntimeError):
        messaging.send_direct_message(1, 2, "hello")
    assert open_count["n"] == 1  # still only one transaction was ever opened
    assert conn.committed is False
    assert conn.rolled_back is True


def test_send_direct_message_does_not_swallow_db_exceptions(monkeypatch):
    _atomic_fake_db_cursor(
        monkeypatch, messaging,
        [[(True,)], [(3,)], []],
        fail_on_call_index=2, fail_exc=ValueError("boom"),
    )
    with pytest.raises(ValueError):
        messaging.send_direct_message(1, 2, "hello")


# ── get_inbox / get_thread / mark_conversation_read / count_unread ──

def test_get_inbox_scopes_to_viewer_only(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, messaging, [[]])
    messaging.get_inbox(7, limit=50)
    sql, params = calls[0]
    assert "c.user1_id = %(uid)s OR c.user2_id = %(uid)s" in sql
    assert params["uid"] == 7
    assert params["limit"] == 50


def test_get_inbox_orders_by_latest_activity(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, messaging, [[]])
    messaging.get_inbox(7)
    assert "ORDER BY lm.created_at DESC" in calls[0][0]


def test_get_inbox_maps_unread_count_correctly(monkeypatch):
    _sequential_db_cursor(monkeypatch, messaging, [[
        (3, 2, "alice", "https://img/x.jpg", "hey!", datetime.datetime(2026, 1, 1), 2, 4),
    ]])
    inbox = messaging.get_inbox(1)
    assert inbox[0]["conversation_id"] == 3
    assert inbox[0]["other_id"] == 2
    assert inbox[0]["username"] == "alice"
    assert inbox[0]["unread_count"] == 4


def test_get_thread_resolves_only_the_viewer_target_pair(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, messaging, [
        [(3,)],   # get_conversation_between lookup
        [],       # message rows
    ])
    conv_id, msgs = messaging.get_thread(5, 2, limit=200)
    assert conv_id == 3
    assert calls[0][1] == (2, 5)  # canonicalised pair, not raw args


def test_get_thread_returns_empty_when_no_conversation_exists(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, messaging, [[]])
    conv_id, msgs = messaging.get_thread(5, 2)
    assert conv_id is None
    assert msgs == []
    assert len(calls) == 1  # never queries direct_messages for a nonexistent conversation


def test_mark_conversation_read_scopes_to_conversation_and_recipient(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, messaging, [[]])
    messaging.mark_conversation_read(3, 1)
    sql, params = calls[0]
    assert "conversation_id = %s AND recipient_id = %s AND is_read = FALSE" in sql
    assert params == (3, 1)


def test_mark_conversation_read_never_touches_sender_id():
    import inspect
    src = inspect.getsource(messaging.mark_conversation_read)
    assert "sender_id" not in src


def test_count_unread_messages_scopes_to_recipient_only(monkeypatch):
    calls = _sequential_db_cursor(monkeypatch, messaging, [[(5,)]])
    assert messaging.count_unread_messages(1) == 5
    sql, params = calls[0]
    assert "recipient_id = %s AND is_read = FALSE" in sql
    assert params == (1,)


# ── /messages inbox route ───────────────────────────────────────────

def _conv(conversation_id=3, other_id=2, username="alice", profile_image_url="",
          last_body="hey", last_created_at=None, last_sender_id=2, unread_count=0):
    return {
        "conversation_id": conversation_id, "other_id": other_id, "username": username,
        "profile_image_url": profile_image_url, "last_body": last_body,
        "last_created_at": last_created_at or datetime.datetime.utcnow(),
        "last_sender_id": last_sender_id, "unread_count": unread_count,
    }


def test_messages_inbox_requires_login():
    client = dashboard.app.test_client()
    resp = client.get("/messages")
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("Location", "")


def test_messages_inbox_renders_for_authenticated_user(monkeypatch):
    monkeypatch.setattr(views_messages, "get_inbox", lambda uid, limit=50: [_conv()])
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.get("/messages")
    assert resp.status_code == 200
    assert b"Messages" in resp.data
    assert b"alice" in resp.data


def test_messages_inbox_empty_state(monkeypatch):
    monkeypatch.setattr(views_messages, "get_inbox", lambda uid, limit=50: [])
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/messages").data.decode()
    assert "No messages yet" in html
    assert 'href="/discover"' in html


def test_messages_inbox_empty_state_explains_mutual_follow_requirement(monkeypatch):
    monkeypatch.setattr(views_messages, "get_inbox", lambda uid, limit=50: [])
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/messages").data.decode()
    assert "You'll be able to message someone once you both follow each other." in html


def test_opening_inbox_does_not_mark_anything_read(monkeypatch):
    monkeypatch.setattr(views_messages, "get_inbox", lambda uid, limit=50: [_conv(unread_count=2)])
    called = {"n": 0}
    monkeypatch.setattr(views_messages, "mark_conversation_read", lambda *a: called.update(n=called["n"] + 1))
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    client.get("/messages")
    assert called["n"] == 0


# ── /messages/u/<username> thread route (GET) ───────────────────────

def _mock_thread_route(monkeypatch, conversation_id=None, mutual=True, messages=None, already_following=False):
    monkeypatch.setattr(views_messages, "get_conversation_between",
                         lambda a, b, create=False: conversation_id)
    monkeypatch.setattr(views_messages, "can_users_message", lambda a, b: mutual)
    monkeypatch.setattr(views_messages, "get_thread",
                         lambda uid, other_id, limit=200: (conversation_id, messages or []))
    monkeypatch.setattr(views_messages, "mark_conversation_read", lambda *a: None)
    monkeypatch.setattr(views_messages, "is_following", lambda a, b: already_following)
    monkeypatch.setattr(User, "get_by_username",
                         classmethod(lambda cls, u: OTHER if u == "alice" else None))


def test_thread_requires_login():
    client = dashboard.app.test_client()
    resp = client.get("/messages/u/alice")
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("Location", "")


def test_thread_missing_target_404s(monkeypatch):
    monkeypatch.setattr(User, "get_by_username", classmethod(lambda cls, u: None))
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.get("/messages/u/ghost")
    assert resp.status_code == 404


def test_thread_with_self_is_rejected(monkeypatch):
    monkeypatch.setattr(User, "get_by_username", classmethod(lambda cls, u: OWNER if u == "dimos" else None))
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    resp = client.get("/messages/u/dimos")
    assert resp.status_code == 404


def test_mutual_follow_user_can_open_new_empty_thread(monkeypatch):
    _mock_thread_route(monkeypatch, conversation_id=None, mutual=True, messages=[])
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.get("/messages/u/alice")
    assert resp.status_code == 200
    assert b"Say hello" in resp.data
    assert b'name="body"' in resp.data


def test_non_mutual_user_with_no_history_gets_403(monkeypatch):
    _mock_thread_route(monkeypatch, conversation_id=None, mutual=False)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.get("/messages/u/alice")
    assert resp.status_code == 403


def test_locked_thread_renders_branded_page_not_raw_403(monkeypatch):
    _mock_thread_route(monkeypatch, conversation_id=None, mutual=False, already_following=False)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.get("/messages/u/alice")
    html = resp.data.decode()
    assert resp.status_code == 403
    assert "You can message alice once you both follow each other" in html
    assert 'action="/u/alice/follow"' in html
    assert '>Follow<' in html
    assert 'href="/u/alice"' in html


def test_locked_thread_varies_copy_when_already_following(monkeypatch):
    _mock_thread_route(monkeypatch, conversation_id=None, mutual=False, already_following=True)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/messages/u/alice").data.decode()
    assert "follow you back" in html
    assert '>Following<' in html


def test_existing_conversation_stays_readable_after_unfollow(monkeypatch):
    old_message = {"id": 1, "sender_id": 2, "recipient_id": 1, "body": "still here",
                    "is_read": True, "created_at": datetime.datetime.utcnow()}
    _mock_thread_route(monkeypatch, conversation_id=3, mutual=False, messages=[old_message])
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.get("/messages/u/alice")
    assert resp.status_code == 200
    assert b"still here" in resp.data


def test_composer_absent_when_mutual_follow_has_broken(monkeypatch):
    _mock_thread_route(monkeypatch, conversation_id=3, mutual=False, messages=[])
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/messages/u/alice").data.decode()
    assert "Follow each other to continue messaging." in html
    assert "<textarea" not in html


def test_opening_thread_marks_its_incoming_messages_read(monkeypatch):
    seen = {}
    _mock_thread_route(monkeypatch, conversation_id=3, mutual=True, messages=[])
    monkeypatch.setattr(views_messages, "mark_conversation_read",
                         lambda conv_id, reader_id: seen.update(conv_id=conv_id, reader_id=reader_id))
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    client.get("/messages/u/alice")
    assert seen == {"conv_id": 3, "reader_id": OWNER.id}


def test_opening_new_empty_thread_does_not_call_mark_read(monkeypatch):
    called = {"n": 0}
    _mock_thread_route(monkeypatch, conversation_id=None, mutual=True, messages=[])
    monkeypatch.setattr(views_messages, "mark_conversation_read", lambda *a: called.update(n=called["n"] + 1))
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    client.get("/messages/u/alice")
    assert called["n"] == 0


def test_message_html_escapes_script_tags(monkeypatch):
    hostile = {"id": 1, "sender_id": 2, "recipient_id": 1, "body": "<script>alert(1)</script>",
               "is_read": True, "created_at": datetime.datetime.utcnow()}
    _mock_thread_route(monkeypatch, conversation_id=3, mutual=True, messages=[hostile])
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/messages/u/alice").data.decode()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_message_body_preserves_bold_tag_as_plain_text(monkeypatch):
    literal = {"id": 1, "sender_id": 2, "recipient_id": 1, "body": "<b>hello</b>",
               "is_read": True, "created_at": datetime.datetime.utcnow()}
    _mock_thread_route(monkeypatch, conversation_id=3, mutual=True, messages=[literal])
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/messages/u/alice").data.decode()
    assert "<b>hello</b>" not in html
    assert "&lt;b&gt;hello&lt;/b&gt;" in html


def test_message_text_not_rendered_through_markdown_or_safe():
    with open("templates/messages_thread.html") as f:
        src = f.read()
    assert "m.body | markdown" not in src
    assert "m.body | safe" not in src
    assert "{{ m.body }}" in src  # plain, auto-escaped Jinja output


def test_multiline_message_preserves_line_breaks_via_pre_wrap():
    with open("templates/messages_thread.html") as f:
        src = f.read()
    assert "white-space: pre-wrap" in src


# ── POST /messages/u/<username> send route ──────────────────────────

def test_send_route_requires_login():
    client = dashboard.app.test_client()
    resp = client.post("/messages/u/alice", data={"body": "hi"})
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("Location", "")


def test_send_route_cannot_spoof_sender_id(monkeypatch):
    seen = {}
    monkeypatch.setattr(User, "get_by_username", classmethod(lambda cls, u: OTHER if u == "alice" else None))
    monkeypatch.setattr(views_messages, "send_direct_message",
                         lambda sender_id, recipient_id, body: seen.update(sender=sender_id, recipient=recipient_id))
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    client.post("/messages/u/alice", data={"body": "hi", "sender_id": "999"})
    assert seen["sender"] == OWNER.id


def test_send_route_cannot_spoof_recipient_id(monkeypatch):
    seen = {}
    monkeypatch.setattr(User, "get_by_username", classmethod(lambda cls, u: OTHER if u == "alice" else None))
    monkeypatch.setattr(views_messages, "send_direct_message",
                         lambda sender_id, recipient_id, body: seen.update(sender=sender_id, recipient=recipient_id))
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    client.post("/messages/u/alice", data={"body": "hi", "recipient_id": "999"})
    assert seen["recipient"] == OTHER.id


def test_send_route_target_must_exist(monkeypatch):
    monkeypatch.setattr(User, "get_by_username", classmethod(lambda cls, u: None))
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    resp = client.post("/messages/u/ghost", data={"body": "hi"})
    assert resp.status_code == 404


def test_send_route_to_self_404s(monkeypatch):
    monkeypatch.setattr(User, "get_by_username", classmethod(lambda cls, u: OWNER if u == "dimos" else None))
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    resp = client.post("/messages/u/dimos", data={"body": "hi"})
    assert resp.status_code == 404


def test_successful_send_redirects_to_thread_with_messages_end_anchor(monkeypatch):
    monkeypatch.setattr(User, "get_by_username", classmethod(lambda cls, u: OTHER if u == "alice" else None))
    monkeypatch.setattr(views_messages, "send_direct_message", lambda a, b, c: 1)
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    resp = client.post("/messages/u/alice", data={"body": "hi"})
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/messages/u/alice#messages-end"


def test_no_generic_next_redirect_in_views_messages():
    import inspect
    src = inspect.getsource(views_messages)
    assert 'request.form.get("next"' not in src
    assert 'request.args.get("next"' not in src


# ── profile Message action ───────────────────────────────────────────

def _mock_profile_route(monkeypatch, user, can_message_result=False, following_result=False):
    monkeypatch.setattr(profiles, "get_user_searched_artists", lambda uid: [])
    monkeypatch.setattr(profiles, "get_follow_counts", lambda uid: (0, 0))
    monkeypatch.setattr(profiles, "get_user_posts", lambda uid, viewer_id=None, **k: [])
    monkeypatch.setattr(profiles, "is_following", lambda a, b: following_result)
    monkeypatch.setattr(profiles, "can_users_message", lambda a, b: can_message_result)
    monkeypatch.setattr(User, "get_by_username",
                         classmethod(lambda cls, u: user if u == user.username else None))


def test_profile_message_button_appears_for_mutual_follow(monkeypatch):
    _mock_profile_route(monkeypatch, OTHER, can_message_result=True)
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    html = client.get("/u/alice").data.decode()
    assert ">Message<" in html
    assert 'href="/messages/u/alice#messages-end"' in html


def test_profile_message_button_absent_without_mutual_follow(monkeypatch):
    _mock_profile_route(monkeypatch, OTHER, can_message_result=False)
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    html = client.get("/u/alice").data.decode()
    assert ">Message<" not in html


def test_profile_shows_follow_back_hint_when_viewer_already_follows(monkeypatch):
    _mock_profile_route(monkeypatch, OTHER, can_message_result=False, following_result=True)
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    html = client.get("/u/alice").data.decode()
    assert "Message unlocks once they follow you back." in html
    assert ">Message<" not in html


def test_profile_hides_message_hint_when_viewer_does_not_follow_yet(monkeypatch):
    _mock_profile_route(monkeypatch, OTHER, can_message_result=False, following_result=False)
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    html = client.get("/u/alice").data.decode()
    assert "Message unlocks once they follow you back." not in html


def test_profile_message_button_absent_on_own_profile(monkeypatch):
    _mock_profile_route(monkeypatch, OWNER, can_message_result=True)
    client = dashboard.app.test_client()
    _login(client, monkeypatch, OWNER)
    html = client.get("/u/dimos").data.decode()
    assert ">Message<" not in html


# ── navigation / badges / command palette ───────────────────────────

def test_desktop_header_has_messages_link(monkeypatch):
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/about").data.decode()
    dock = html[html.index('<header class="wv-header"'):html.index("</header>")]
    assert 'href="/messages"' in dock
    assert "wv-msgicon" in dock


def test_desktop_unread_message_badge_renders(monkeypatch):
    monkeypatch.setattr(dashboard, "count_unread_messages", lambda uid: 5)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/about").data.decode()
    dock = html[html.index('<header class="wv-header"'):html.index("</header>")]
    assert 'aria-label="Messages (5 unread)"' in dock
    assert 'class="wv-badge">5' in dock


def test_mobile_more_has_messages_with_unread_badge(monkeypatch):
    monkeypatch.setattr(dashboard, "count_unread_messages", lambda uid: 2)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/about").data.decode()
    more_panel = html[html.index('id="wv-morepanel"'):html.index('id="wv-palette-overlay"')]
    assert 'href="/messages"' in more_panel
    assert 'id="wv-msg-badge-mobile"' in more_panel


def test_profile_menu_has_messages_link(monkeypatch):
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/about").data.decode()
    panel = html[html.index('id="wv-profile-menu"'):html.index("</div>", html.index('id="wv-profile-menu"'))]
    assert 'href="/messages"' in panel
    assert ">Messages<" in panel


def test_command_palette_authenticated_items_include_messages():
    with open("static/js/command-palette.js") as f:
        js = f.read()
    assert '{ label: "Messages", href: "/messages"' in js
    # must live inside authItems(), not the anonymous branch
    auth_fn = js[js.index("function authItems"):js.index("function loadArtists")]
    assert '"Messages"' in auth_fn


def test_existing_notification_badge_still_works_alongside_messages(monkeypatch):
    monkeypatch.setattr(dashboard, "count_unread", lambda uid: 4)
    monkeypatch.setattr(dashboard, "count_unread_messages", lambda uid: 0)
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/about").data.decode()
    assert 'class="wv-badge">4' in html


# ── analytics privacy ────────────────────────────────────────────────

def test_analytics_never_stores_message_thread_path():
    assert analytics._sanitize_analytics_path("/messages/u/alice_music") == "/messages/thread"
    assert "alice_music" not in analytics._sanitize_analytics_path("/messages/u/alice_music")


def test_analytics_leaves_inbox_path_untouched():
    assert analytics._sanitize_analytics_path("/messages") == "/messages"


def test_analytics_discards_message_thread_referrer():
    referrer = "https://waveline.example.com/messages/u/alice_music"
    sanitized = analytics._sanitize_analytics_referrer(referrer)
    assert sanitized is None


def test_analytics_leaves_unrelated_referrer_untouched():
    referrer = "https://waveline.example.com/discover"
    assert analytics._sanitize_analytics_referrer(referrer) == referrer


def test_record_pageview_sanitizes_path_before_insert(monkeypatch):
    from flask import Response

    calls = []

    class FakeCur:
        def execute(self, sql, params=None):
            calls.append(params)

    @contextlib.contextmanager
    def fake_cm(commit=False):
        yield FakeCur()

    monkeypatch.setattr(analytics, "db_cursor", fake_cm)
    response = Response("ok", content_type="text/html")

    # A real UA — an empty one is now (correctly) treated as bot-like and
    # would never reach the INSERT this test is actually checking.
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    with dashboard.app.test_request_context("/messages/u/alice_music", method="GET", headers=headers):
        analytics.record_pageview(response)

    assert calls, "expected an analytics INSERT to have been attempted"
    inserted_path = calls[-1][0]
    assert inserted_path == "/messages/thread"
    assert "alice_music" not in inserted_path


# ── avatar fallback reuse (Phase 3/5 pattern) ────────────────────────

def test_inbox_avatar_has_single_wrapper_with_genuinely_hidden_fallback(monkeypatch):
    monkeypatch.setattr(views_messages, "get_inbox", lambda uid, limit=50: [
        _conv(other_id=77, username="hasimage", profile_image_url="https://res.cloudinary.com/demo/hasimage.jpg")
    ])
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/messages").data.decode()

    assert html.count('class="dm-avatar"') == 1
    avatar_start = html.index('class="dm-avatar"')
    avatar_end = html.index('class="dm-body"', avatar_start)
    avatar_block = html[avatar_start:avatar_end]
    assert "<img" in avatar_block
    assert 'id="dm-fallback-inbox-77"' in avatar_block
    assert "display:none" in avatar_block or "display: none" in avatar_block
    assert " hidden" not in avatar_block.split('id="dm-fallback-inbox-77"')[1].split(">")[0]


def test_thread_avatar_falls_back_to_monogram_without_image(monkeypatch):
    target = User(id=2, username="alice", email="a@e.com", password_hash="x", profile_image_url="")
    _mock_thread_route(monkeypatch, conversation_id=None, mutual=True, messages=[])
    monkeypatch.setattr(User, "get_by_username", classmethod(lambda cls, u: target if u == "alice" else None))
    client = dashboard.app.test_client()
    _login(client, monkeypatch)
    html = client.get("/messages/u/alice").data.decode()
    head_start = html.index('class="dm-thread-head"')
    head_end = html.index('class="dm-thread-list"')
    head_block = html[head_start:head_end]
    assert 'class="wv-avatar wv-avatar-sm"' in head_block
    assert "<img" not in head_block

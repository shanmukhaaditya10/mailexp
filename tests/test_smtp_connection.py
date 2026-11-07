"""SMTP connection reuse and drop handling for real (queued) sends.

The whole SMTP layer is faked: no socket is opened and no real email is sent.
We exercise the real ``SmtpSession`` / ``send_email`` code path by swapping
``gmail_send.smtplib`` for a fake, so connection reuse and the ambiguous-outcome
rule are tested end to end.
"""
import smtplib
import time
import types

from app import gmail_send
from app import sender as sender_mod
from app.db import get_conn
from app.services import drafts as drafts_svc
from tests.conftest import add_founder, add_lead


def _install_fake_smtp(monkeypatch, *, drop_on_attempt=None):
    """Patch smtplib.SMTP_SSL; return (connections, attempts) bookkeeping."""
    connections = []
    attempts = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=60, local_hostname=None):
            self.host = host
            self.port = port
            self.index = len(connections)
            self.logins = 0
            connections.append(self)

        def login(self, user, pwd):
            self.logins += 1

        def send_message(self, msg):
            n = len(attempts)
            attempts.append({"conn": self.index, "subject": msg["Subject"]})
            if drop_on_attempt is not None and n == drop_on_attempt:
                # Socket dies mid-transaction: the outcome is unknowable.
                raise smtplib.SMTPServerDisconnected("connection lost")

        def quit(self):
            pass

    # The autouse fixture strips Gmail env; the fake SMTP never uses these, but
    # the real send_email path requires them to be present.
    monkeypatch.setenv("GMAIL_ADDRESS", "sender@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-password")
    monkeypatch.setattr(gmail_send, "smtplib", types.SimpleNamespace(SMTP_SSL=FakeSMTP))
    return connections, attempts


def _seed_drafts(count, *, cap="40", interval="0"):
    """Create `count` distinct leads/founders/drafts and return their ids."""
    c = get_conn()
    try:
        c.execute("UPDATE settings SET value='' WHERE key='send_window'")
        c.execute("UPDATE settings SET value=? WHERE key='daily_cap'", (cap,))
        c.execute(
            "UPDATE settings SET value=? WHERE key='min_send_interval_seconds'", (interval,)
        )
        ids = []
        for i in range(count):
            lid = add_lead(c, company=f"Co{i}")
            add_founder(c, lid, email=f"founder{i}@example.com")
            ids.append(drafts_svc.build_draft(c, lid)["id"])
        return ids
    finally:
        c.close()


def _await_job(job, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = next(j for j in sender_mod.list_jobs(200) if j["id"] == job["id"])
        if current["status"] in ("done", "cancelled"):
            return current
        time.sleep(0.02)
    raise AssertionError(f"job did not finish: {current['status']}")


def test_batch_reuses_one_connection_and_authenticates_once(temp_db, monkeypatch):
    ids = _seed_drafts(5)
    connections, attempts = _install_fake_smtp(monkeypatch)

    job = _await_job(sender_mod.enqueue(ids, dry_run=False))

    assert job["done"] == 5
    assert job["summary"].get("sent") == 5
    assert len(connections) == 1  # connect + login paid once for the batch
    assert connections[0].logins == 1
    assert len(attempts) == 5


def test_forty_message_batch_uses_single_connection(temp_db, monkeypatch):
    ids = _seed_drafts(40)
    connections, attempts = _install_fake_smtp(monkeypatch)

    job = _await_job(sender_mod.enqueue(ids, dry_run=False))

    assert job["done"] == 40
    assert job["summary"].get("sent") == 40
    assert len(connections) == 1
    assert len(attempts) == 40


def test_dry_run_opens_no_connection(temp_db, monkeypatch):
    ids = _seed_drafts(3)
    connections, attempts = _install_fake_smtp(monkeypatch)

    job = _await_job(sender_mod.enqueue(ids, dry_run=True))

    assert job["done"] == 3
    assert connections == []
    assert attempts == []


def test_connection_drop_marks_unknown_and_never_resends(temp_db, monkeypatch):
    ids = _seed_drafts(5)
    connections, attempts = _install_fake_smtp(monkeypatch, drop_on_attempt=2)

    job = _await_job(sender_mod.enqueue(ids, dry_run=False))

    assert job["done"] == 5
    assert job["summary"].get("send_unknown") == 1
    assert job["summary"].get("sent") == 4
    # The dropped message was attempted exactly once (never blindly retried)...
    assert len(attempts) == 5
    # ...and the following message reconnected instead of reusing a dead socket.
    assert len(connections) == 2

    c = get_conn()
    try:
        row = c.execute(
            "SELECT status, client_message_id FROM messages WHERE id=?", (ids[2],)
        ).fetchone()
    finally:
        c.close()

    assert row["status"] == "send_unknown"
    assert row["client_message_id"]  # persisted for later reconciliation


def test_transient_auth_failure_is_not_ambiguous(temp_db, monkeypatch):
    """A failure *before* submission (login refused) is a normal failure."""
    ids = _seed_drafts(1)
    connections = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=60, local_hostname=None):
            connections.append(self)

        def login(self, user, pwd):
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

        def send_message(self, msg):  # pragma: no cover - must not be reached
            raise AssertionError("no message should be submitted")

        def quit(self):
            pass

    monkeypatch.setenv("GMAIL_ADDRESS", "sender@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-password")
    monkeypatch.setattr(gmail_send, "smtplib", types.SimpleNamespace(SMTP_SSL=FakeSMTP))

    job = _await_job(sender_mod.enqueue(ids, dry_run=False))

    assert job["summary"].get("send_failed") == 1
    c = get_conn()
    try:
        row = c.execute("SELECT status FROM messages WHERE id=?", (ids[0],)).fetchone()
    finally:
        c.close()
    assert row["status"] == "send_failed"

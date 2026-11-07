"""Concurrency, duplicate-prevention, rate-limit and reconciliation tests.

Everything here uses a temporary SQLite database and mocked SMTP/IMAP, so no
network or Gmail account is involved.
"""
import threading

import pytest

from app.services import sending
from tests.conftest import add_founder, add_lead, add_message, set_setting


def _mock_send(monkeypatch, calls, *, fail=None):
    def fake(*a, **k):
        calls.append(k.get("message_id"))
        if fail is not None:
            raise fail
        return {"message_id": k.get("message_id") or "<sent@x>", "attached": False}
    monkeypatch.setattr(sending.gmail_send, "send_email", fake)


# --------------------------------------------------------------------------
# duplicate prevention
# --------------------------------------------------------------------------

def test_only_one_thread_can_claim_a_message(conn):
    from app.db import get_conn

    lead = add_lead(conn)
    mid = add_message(conn, lead)

    results = []
    barrier = threading.Barrier(6)

    def worker():
        c = get_conn()
        try:
            barrier.wait()
            results.append(sending.claim(c, mid, enforce_cap=False))
        finally:
            c.close()

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(1 for r in results if r) == 1


def test_repeated_clicks_send_once(conn, monkeypatch):
    set_setting(conn, "send_window", "")
    calls = []
    _mock_send(monkeypatch, calls)
    lead = add_lead(conn)
    mid = add_message(conn, lead)

    assert sending.send_message(conn, mid, False)["outcome"] == "sent"
    with pytest.raises(sending.SendError):
        sending.send_message(conn, mid, False)
    assert len(calls) == 1


def test_duplicate_sequence_step_is_blocked(conn, monkeypatch):
    set_setting(conn, "send_window", "")
    calls = []
    _mock_send(monkeypatch, calls)
    lead = add_lead(conn)
    # A previous, already-sent message for step 0 of this lead.
    conn.execute(
        "INSERT INTO messages (lead_id, step, to_addrs, subject, body, status) "
        "VALUES (?,0,?,?,?,'sent')",
        (lead, "jane@acme.com", "Hi", "x"),
    )
    conn.commit()
    mid = add_message(conn, lead, to="jane@acme.com", step=0)

    with pytest.raises(sending.SendError):
        sending.send_message(conn, mid, False)
    assert calls == []  # refused before any SMTP submission


def test_unique_step_index_is_db_enforced(conn):
    import sqlite3

    lead = add_lead(conn)
    conn.execute(
        "INSERT INTO messages (lead_id, step, to_addrs, status) VALUES (?,0,?, 'sent')",
        (lead, "a@b.com"),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO messages (lead_id, step, to_addrs, status) VALUES (?,0,?, 'sent')",
            (lead, "c@d.com"),
        )


# --------------------------------------------------------------------------
# ambiguous outcomes and restarts
# --------------------------------------------------------------------------

def test_smtp_timeout_after_acceptance_is_never_resent(conn, monkeypatch):
    set_setting(conn, "send_window", "")
    calls = []
    _mock_send(monkeypatch, calls, fail=sending.gmail_send.DeliveryError("timeout", submitted=True))
    lead = add_lead(conn)
    mid = add_message(conn, lead)

    with pytest.raises(sending.SendError):
        sending.send_message(conn, mid, False)
    row = conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    assert row["status"] == "send_unknown"
    assert row["client_message_id"]  # persisted before the attempt

    # Every later attempt refuses; the SMTP layer is never called again.
    for _ in range(3):
        with pytest.raises(sending.SendError):
            sending.send_message(conn, mid, False)
    assert len(calls) == 1


def test_retry_after_restart_does_not_resend(conn, monkeypatch):
    calls = []
    _mock_send(monkeypatch, calls)
    lead = add_lead(conn)
    mid = add_message(conn, lead)
    # Simulate a crash: the row was claimed (sending) and the id persisted.
    conn.execute(
        "UPDATE messages SET status='sending', client_message_id='<crashed@x>' WHERE id=?",
        (mid,),
    )
    conn.commit()

    assert sending.recover_stale_sending(conn) == 1
    row = conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    assert row["status"] == "send_unknown"

    # Restart must not resend it.
    with pytest.raises(sending.SendError):
        sending.send_message(conn, mid, False)
    assert calls == []


def test_reconcile_finds_message_in_gmail(conn, monkeypatch):
    lead = add_lead(conn)
    mid = add_message(conn, lead, status="send_unknown")
    conn.execute("UPDATE messages SET client_message_id='<found@x>' WHERE id=?", (mid,))
    conn.commit()
    calls = []
    _mock_send(monkeypatch, calls)
    monkeypatch.setattr(sending.gmail_read, "search_sent_by_message_id", lambda m: True)

    result = sending.reconcile_unknown(conn, mid)
    assert result["found"] is True and result["reconciled"] is True
    assert result["message"]["status"] == "sent"
    assert calls == []  # reconciled, never resent


def test_reconcile_cannot_find_leaves_unknown(conn, monkeypatch):
    lead = add_lead(conn)
    mid = add_message(conn, lead, status="send_unknown")
    conn.execute("UPDATE messages SET client_message_id='<missing@x>' WHERE id=?", (mid,))
    conn.commit()
    monkeypatch.setattr(sending.gmail_read, "search_sent_by_message_id", lambda m: False)
    result = sending.reconcile_unknown(conn, mid)
    assert result["found"] is False and result["reconciled"] is False
    assert result["message"]["status"] == "send_unknown"


def test_reconcile_treats_imap_error_as_uncertain(conn, monkeypatch):
    lead = add_lead(conn)
    mid = add_message(conn, lead, status="send_unknown")
    conn.execute("UPDATE messages SET client_message_id='<x@x>' WHERE id=?", (mid,))
    conn.commit()

    def boom(m):
        raise RuntimeError("imap down")

    monkeypatch.setattr(sending.gmail_read, "search_sent_by_message_id", boom)
    result = sending.reconcile_unknown(conn, mid)
    assert result["found"] is False
    assert result["message"]["status"] == "send_unknown"


# --------------------------------------------------------------------------
# rate limiting
# --------------------------------------------------------------------------

def test_concurrent_sends_cannot_exceed_cap(temp_db, monkeypatch):
    from app.db import get_conn

    c = get_conn()
    c.execute("UPDATE settings SET value='' WHERE key='send_window'")
    c.execute("UPDATE settings SET value='2' WHERE key='daily_cap'")
    ids = []
    for i in range(8):
        lid = add_lead(c, company=f"C{i}")
        ids.append(add_message(c, lid, to=f"a{i}@b.com"))
    c.close()

    calls = []
    _mock_send(monkeypatch, calls)

    barrier = threading.Barrier(len(ids))
    outcomes = []
    lock = threading.Lock()

    def worker(mid):
        cc = get_conn()
        try:
            barrier.wait()
            try:
                r = sending.send_message(cc, mid, False)
                out = r["outcome"]
            except sending.SendSkipped:
                out = "skipped"
            except sending.SendError:
                out = "error"
        finally:
            cc.close()
        with lock:
            outcomes.append(out)

    threads = [threading.Thread(target=worker, args=(m,)) for m in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes.count("sent") <= 2
    check = get_conn()
    try:
        assert sending.reserved_today(check) <= 2
        assert sending.count_sent_today(check) <= 2
    finally:
        check.close()


def test_dry_run_does_not_consume_quota(conn):
    set_setting(conn, "daily_cap", "0")  # no real sends allowed at all
    set_setting(conn, "send_window", "")
    lead = add_lead(conn)
    mid = add_message(conn, lead)

    assert sending.send_message(conn, mid, dry_run=True)["outcome"] == "dry_run"
    assert sending.reserved_today(conn) == 0
    assert sending.count_sent_today(conn) == 0


def test_interval_zero_without_prior_send(conn):
    set_setting(conn, "min_send_interval_seconds", "20")
    assert sending.seconds_until_next_send(conn) == 0.0


def test_interval_blocks_immediate_second_send(conn):
    from datetime import datetime

    set_setting(conn, "min_send_interval_seconds", "20")
    lead = add_lead(conn)
    conn.execute(
        "INSERT INTO messages (lead_id, step, to_addrs, status, sent_at) "
        "VALUES (?,0,?, 'sent', ?)",
        (lead, "a@b.com", datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    wait = sending.seconds_until_next_send(conn)
    assert 0 < wait <= 20


def test_interval_elapsed_allows_send(conn):
    from datetime import datetime, timedelta

    set_setting(conn, "min_send_interval_seconds", "20")
    lead = add_lead(conn)
    old = (datetime.now() - timedelta(seconds=100)).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO messages (lead_id, step, to_addrs, status, sent_at) "
        "VALUES (?,0,?, 'sent', ?)",
        (lead, "a@b.com", old),
    )
    conn.commit()
    assert sending.seconds_until_next_send(conn) == 0.0


def test_interval_paced_from_attempt_start(conn):
    from datetime import datetime

    # An in-flight attempt records its start in last_attempt_at; pacing must use
    # that (not sent_at), so a slow SMTP transaction cannot shrink the interval.
    set_setting(conn, "min_send_interval_seconds", "1")
    lead = add_lead(conn)
    conn.execute(
        "INSERT INTO messages (lead_id, step, to_addrs, status, last_attempt_at) "
        "VALUES (?,0,?, 'sending', ?)",
        (lead, "a@b.com", datetime.now().isoformat(timespec="microseconds")),
    )
    conn.commit()
    wait = sending.seconds_until_next_send(conn)
    assert 0 < wait <= 1


def test_interval_supports_fractional_seconds(conn):
    from datetime import datetime

    set_setting(conn, "min_send_interval_seconds", "0.5")
    lead = add_lead(conn)
    conn.execute(
        "INSERT INTO messages (lead_id, step, to_addrs, status, sent_at) "
        "VALUES (?,0,?, 'sent', ?)",
        (lead, "a@b.com", datetime.now().isoformat(timespec="microseconds")),
    )
    conn.commit()
    wait = sending.seconds_until_next_send(conn)
    assert 0 < wait <= 0.5


def test_worker_spaces_sends_by_interval(temp_db, monkeypatch):
    import time

    from app import sender as sender_mod
    from app.db import get_conn
    from app.services import drafts as drafts_svc

    c = get_conn()
    c.execute("UPDATE settings SET value='' WHERE key='send_window'")
    c.execute("UPDATE settings SET value='50' WHERE key='daily_cap'")
    c.execute("UPDATE settings SET value='0.2' WHERE key='min_send_interval_seconds'")
    ids = []
    for i in range(3):
        lid = add_lead(c, company=f"Paced{i}")
        add_founder(c, lid, email=f"paced{i}@b.com")
        ids.append(drafts_svc.build_draft(c, lid)["id"])
    c.close()

    stamps = []
    def fake(*a, **k):
        stamps.append(time.monotonic())
        return {"message_id": "<paced@x>", "attached": False}
    monkeypatch.setattr(sending.gmail_send, "send_email", fake)

    job = sender_mod.enqueue(ids, dry_run=False)
    for _ in range(400):
        if job["status"] in ("done", "cancelled"):
            break
        time.sleep(0.02)
        job = next(j for j in sender_mod.list_jobs(5) if j["id"] == job["id"])

    assert job["done"] == 3
    gaps = [stamps[i + 1] - stamps[i] for i in range(len(stamps) - 1)]
    assert all(g >= 0.15 for g in gaps), gaps



def test_send_queue_stops_at_cap(temp_db, monkeypatch):
    from app.db import get_conn
    from app.services import drafts as drafts_svc

    calls = []
    _mock_send(monkeypatch, calls)

    c = get_conn()
    c.execute("UPDATE settings SET value='' WHERE key='send_window'")
    c.execute("UPDATE settings SET value='1' WHERE key='daily_cap'")
    c.execute("UPDATE settings SET value='0' WHERE key='min_send_interval_seconds'")
    lead1 = add_lead(c, company="First")
    add_founder(c, lead1, email="jane@acme.com")
    lead2 = add_lead(c, company="Second")
    add_founder(c, lead2, email="john@acme.com")
    d1 = drafts_svc.build_draft(c, lead1)["id"]
    d2 = drafts_svc.build_draft(c, lead2)["id"]
    c.close()

    # Cap is 1: the batch must stop after the first real send.
    from app import sender as sender_mod
    job = sender_mod.enqueue([d1, d2], dry_run=False)
    import time
    for _ in range(200):
        if job["status"] in ("done", "cancelled"):
            break
        time.sleep(0.02)
        job = next(j for j in sender_mod.list_jobs(5) if j["id"] == job["id"])

    assert len(calls) == 1
    assert job["summary"].get("skipped", 0) == 1


# --------------------------------------------------------------------------
# API surface: reconcile + confirmation-gated retry
# --------------------------------------------------------------------------

def _unknown_message(temp_db, client_message_id="<u@x>"):
    from app.db import get_conn

    c = get_conn()
    try:
        lead = add_lead(c, company="Acme")
        mid = add_message(c, lead, status="send_unknown")
        c.execute(
            "UPDATE messages SET client_message_id=? WHERE id=?", (client_message_id, mid)
        )
        c.commit()
    finally:
        c.close()
    return mid


def test_reconcile_endpoint_marks_sent_when_found(client, temp_db, monkeypatch):
    mid = _unknown_message(temp_db)
    monkeypatch.setattr(sending.gmail_read, "search_sent_by_message_id", lambda m: True)
    r = client.post(f"/api/messages/{mid}/reconcile")
    assert r.status_code == 200
    body = r.json()
    assert body["reconciled"] is True
    assert body["message"]["status"] == "sent"


def test_retry_unknown_requires_confirmation(client, temp_db, monkeypatch):
    mid = _unknown_message(temp_db)
    monkeypatch.setattr(sending.gmail_read, "search_sent_by_message_id", lambda m: False)

    r = client.post(f"/api/messages/{mid}/retry", json={"confirm": False})
    assert r.status_code == 409

    r = client.post(f"/api/messages/{mid}/retry", json={"confirm": True})
    assert r.status_code == 200
    assert r.json()["resent"] is True

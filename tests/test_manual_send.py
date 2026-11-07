"""Sending a chosen template to any lead at any time ("manual" sends).

A message built from an explicitly chosen template is marked ``manual`` and is
exempt from the one-per-sequence-step guard, so the same template can be sent
to an already-contacted lead again. Automatic (next-step) sends keep the guard.
All SMTP is mocked; no email is sent.
"""
import itertools

import pytest

from app.services import drafts, sending
from tests.conftest import add_founder, add_lead, set_setting


def _template_id(conn, step):
    return conn.execute(
        "SELECT id FROM templates WHERE step=? ORDER BY id LIMIT 1", (step,)
    ).fetchone()["id"]


def _mock_send(monkeypatch):
    ids = itertools.count(1)
    monkeypatch.setattr(
        sending.gmail_send, "send_email",
        lambda *a, **k: {"message_id": f"<{next(ids)}@x>", "attached": False},
    )


def _prepare(conn, monkeypatch):
    set_setting(conn, "send_window", "")
    set_setting(conn, "daily_cap", "40")
    set_setting(conn, "min_send_interval_seconds", "0")
    _mock_send(monkeypatch)
    lead = add_lead(conn, company="Acme")
    add_founder(conn, lead, email="jane@acme.com")
    return lead


def test_build_draft_with_template_is_marked_manual(conn):
    lead = add_lead(conn, company="Acme")
    add_founder(conn, lead, email="jane@acme.com")
    d = drafts.build_draft(conn, lead, template_id=_template_id(conn, 2))
    assert d["manual"] == 1


def test_build_draft_auto_is_not_manual(conn):
    lead = add_lead(conn, company="Acme")
    add_founder(conn, lead, email="jane@acme.com")
    d = drafts.build_draft(conn, lead)
    assert d["manual"] == 0


def test_manual_resend_of_already_sent_step_is_allowed(conn, monkeypatch):
    lead = _prepare(conn, monkeypatch)
    intro = _template_id(conn, 0)

    # Automatic step 0 -> lead becomes contacted at step 1.
    d0 = drafts.build_draft(conn, lead)
    assert sending.send_message(conn, d0["id"], dry_run=False)["outcome"] == "sent"
    lead_row = conn.execute("SELECT status, step FROM leads WHERE id=?", (lead,)).fetchone()
    assert lead_row["status"] == "contacted" and lead_row["step"] == 1

    # Re-send the same (step 0) template to the contacted lead at any time.
    for _ in range(2):
        d = drafts.build_draft(conn, lead, template_id=intro)
        assert sending.send_message(conn, d["id"], dry_run=False)["outcome"] == "sent"

    sent = conn.execute(
        "SELECT COUNT(*) c FROM messages WHERE lead_id=? AND status='sent'", (lead,)
    ).fetchone()["c"]
    assert sent == 3
    # The cursor never rewinds: still at step 1, not back to 0.
    assert conn.execute("SELECT step FROM leads WHERE id=?", (lead,)).fetchone()["step"] == 1


def test_auto_resend_of_sent_step_is_still_blocked(conn, monkeypatch):
    lead = _prepare(conn, monkeypatch)
    # A previously delivered, non-manual step 0.
    conn.execute(
        "INSERT INTO messages (lead_id, step, to_addrs, subject, body, status, manual) "
        "VALUES (?,0,?,?,?, 'sent', 0)",
        (lead, "jane@acme.com", "Hi", "x"),
    )
    conn.commit()
    # A fresh non-manual draft for the same step.
    mid = conn.execute(
        "INSERT INTO messages (lead_id, step, to_addrs, subject, body, status, manual) "
        "VALUES (?,0,?,?,?, 'draft', 0)",
        (lead, "jane@acme.com", "Hi", "x"),
    ).lastrowid
    conn.commit()

    with pytest.raises(sending.SendError):
        sending.send_message(conn, mid, dry_run=False)


def test_manual_template_allowed_when_sequence_complete(conn):
    # Step 5 ("sequence complete") must still accept a chosen template.
    lead = add_lead(conn, company="Acme", step=5)
    add_founder(conn, lead, email="jane@acme.com")
    d = drafts.build_draft(conn, lead, template_id=_template_id(conn, 0))
    assert d["step"] == 0 and d["manual"] == 1


def test_unique_step_index_allows_two_manual_sends(conn):
    import sqlite3

    lead = add_lead(conn, company="Acme")
    for _ in range(2):
        conn.execute(
            "INSERT INTO messages (lead_id, step, to_addrs, status, manual) "
            "VALUES (?,0,?, 'sent', 1)",
            (lead, "jane@acme.com"),
        )
    conn.commit()  # two manual step-0 sends are allowed

    conn.execute(
        "INSERT INTO messages (lead_id, step, to_addrs, status, manual) "
        "VALUES (?,0,?, 'sent', 0)",
        (lead, "jane@acme.com"),
    )
    conn.commit()  # one non-manual is allowed
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO messages (lead_id, step, to_addrs, status, manual) "
            "VALUES (?,0,?, 'sent', 0)",
            (lead, "jane@acme.com"),
        )


def test_step_index_is_rebuilt_for_manual(temp_db):
    c = temp_db.get_conn()
    try:
        # Simulate a pre-`manual` database: the old index without the predicate.
        c.execute("DROP INDEX idx_messages_sent_step")
        c.execute(
            "CREATE UNIQUE INDEX idx_messages_sent_step ON messages(lead_id, step) "
            "WHERE status='sent'"
        )
        c.commit()
        assert "manual" not in c.execute(
            "SELECT sql FROM sqlite_master WHERE name='idx_messages_sent_step'"
        ).fetchone()["sql"]
    finally:
        c.close()

    temp_db.init_db()  # startup should rebuild it

    c = temp_db.get_conn()
    try:
        sql = c.execute(
            "SELECT sql FROM sqlite_master WHERE name='idx_messages_sent_step'"
        ).fetchone()["sql"]
    finally:
        c.close()
    assert "manual" in sql


def test_next_step_never_rewinds_or_skips(conn):
    from app.services.sending import _next_step

    assert _next_step(0, 0) == 1   # automatic first send
    assert _next_step(1, 0) == 1   # manual re-send of step 0 does not rewind
    assert _next_step(1, 2) == 3   # manual step 2 advances past it
    assert _next_step(3, 1) == 3   # manual earlier step does not rewind
    assert _next_step(4, 4) == 5
    assert _next_step(5, 0) == 5   # capped at 5

from app.services import mailbox_sync
from tests.conftest import add_founder, add_lead


def msg(**kw):
    base = {
        "message_id": "<m1@x>",
        "from": "jane@acme.com",
        "to": [],
        "cc": [],
        "subject": "Re: hello",
        "date": "2026-01-01T00:00:00",
        "thread_id": "",
        "body": "sounds good",
        "auto_submitted": "",
    }
    base.update(kw)
    return base


def test_reply_matched_by_email(conn, monkeypatch):
    lead = add_lead(conn, domain="acme.com")
    add_founder(conn, lead, email="jane@acme.com")
    monkeypatch.setattr(mailbox_sync.gmail_read, "fetch_inbox", lambda limit=50: [msg()])

    result = mailbox_sync.sync_replies(conn)
    assert result["findings"] == 1
    lead_row = conn.execute("SELECT * FROM leads WHERE id=?", (lead,)).fetchone()
    assert lead_row["status"] == "replied"
    reply = conn.execute("SELECT * FROM replies").fetchone()
    assert reply["lead_id"] == lead
    assert reply["kind"] == "reply"


def test_reply_matched_by_thread_id(conn, monkeypatch):
    lead = add_lead(conn, domain="acme.com")
    add_founder(conn, lead, email="jane@acme.com")
    conn.execute(
        "INSERT INTO messages (lead_id, step, to_addrs, subject, body, status, thread_id) "
        "VALUES (?,0,?,?,?,'sent','t1')",
        (lead, "jane@acme.com", "Hi", "x"),
    )
    conn.commit()
    monkeypatch.setattr(
        mailbox_sync.gmail_read, "fetch_inbox",
        lambda limit=50: [msg(**{"from": "someone@elsewhere.com", "thread_id": "t1"})],
    )
    mailbox_sync.sync_replies(conn)
    reply = conn.execute("SELECT * FROM replies").fetchone()
    assert reply["lead_id"] == lead


def test_ambiguous_domain_is_recorded_not_attributed(conn, monkeypatch):
    add_lead(conn, company="A", domain="acme.com")
    add_lead(conn, company="B", domain="acme.com")
    monkeypatch.setattr(
        mailbox_sync.gmail_read, "fetch_inbox",
        lambda limit=50: [msg(**{"from": "stranger@acme.com"})],
    )
    result = mailbox_sync.sync_replies(conn)
    assert result["ambiguous"] == 1
    reply = conn.execute("SELECT * FROM replies").fetchone()
    assert reply["lead_id"] is None
    assert reply["match_note"]
    # Neither lead's sequence should have been stopped.
    statuses = {r["status"] for r in conn.execute("SELECT status FROM leads")}
    assert statuses == {"not_contacted"}


def test_reply_dedup_by_message_id(conn, monkeypatch):
    lead = add_lead(conn, domain="acme.com")
    add_founder(conn, lead, email="jane@acme.com")
    monkeypatch.setattr(mailbox_sync.gmail_read, "fetch_inbox", lambda limit=50: [msg()])
    mailbox_sync.sync_replies(conn)
    mailbox_sync.sync_replies(conn)
    assert conn.execute("SELECT COUNT(*) FROM replies").fetchone()[0] == 1


def test_bounce_stops_sequence(conn, monkeypatch):
    lead = add_lead(conn, domain="acme.com")
    add_founder(conn, lead, email="jane@acme.com")
    bounce = msg(
        **{"from": "mailer-daemon@googlemail.com", "subject": "Delivery Status Notification (Failure)", "body": "Your message to jane@acme.com could not be delivered."}
    )
    monkeypatch.setattr(mailbox_sync.gmail_read, "fetch_inbox", lambda limit=50: [bounce])
    mailbox_sync.sync_replies(conn)
    reply = conn.execute("SELECT * FROM replies").fetchone()
    assert reply["kind"] == "bounce"
    assert reply["lead_id"] == lead
    assert conn.execute("SELECT status FROM leads WHERE id=?", (lead,)).fetchone()["status"] == "bounced"


def test_out_of_office_does_not_stop_sequence(conn, monkeypatch):
    lead = add_lead(conn, domain="acme.com")
    add_founder(conn, lead, email="jane@acme.com")
    ooo = msg(subject="Out of office", auto_submitted="auto-replied")
    monkeypatch.setattr(mailbox_sync.gmail_read, "fetch_inbox", lambda limit=50: [ooo])
    mailbox_sync.sync_replies(conn)
    reply = conn.execute("SELECT * FROM replies").fetchone()
    assert reply["kind"] == "ooo"
    assert conn.execute("SELECT status FROM leads WHERE id=?", (lead,)).fetchone()["status"] == "not_contacted"


def test_sync_sent_creates_message(conn, monkeypatch):
    lead = add_lead(conn, domain="acme.com")
    add_founder(conn, lead, email="jane@acme.com")
    sent = msg(**{"from": "me@me.com", "to": ["jane@acme.com"], "message_id": "<s1@me>"})
    monkeypatch.setattr(mailbox_sync.gmail_read, "fetch_sent", lambda limit=50: [sent])
    result = mailbox_sync.sync_sent(conn)
    assert result["new_messages"] == 1
    row = conn.execute("SELECT * FROM messages WHERE message_id='<s1@me>'").fetchone()
    assert row["lead_id"] == lead
    assert row["status"] == "sent"


def test_sync_sent_dedup(conn, monkeypatch):
    lead = add_lead(conn, domain="acme.com")
    add_founder(conn, lead, email="jane@acme.com")
    sent = msg(**{"from": "me@me.com", "to": ["jane@acme.com"], "message_id": "<s1@me>"})
    monkeypatch.setattr(mailbox_sync.gmail_read, "fetch_sent", lambda limit=50: [sent])
    mailbox_sync.sync_sent(conn)
    mailbox_sync.sync_sent(conn)
    assert conn.execute("SELECT COUNT(*) FROM messages WHERE message_id='<s1@me>'").fetchone()[0] == 1

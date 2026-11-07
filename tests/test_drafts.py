import pytest

from app.services import drafts
from tests.conftest import add_founder, add_lead, add_message


def test_build_draft_creates_and_replaces(conn):
    lead = add_lead(conn, company="Acme")
    add_founder(conn, lead, email="jane@acme.com")

    first = drafts.build_draft(conn, lead)
    assert first["status"] == "draft"
    assert first["to_addrs"] == "jane@acme.com"

    second = drafts.build_draft(conn, lead)
    assert second["id"] != first["id"]
    remaining = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE lead_id=? AND status='draft'", (lead,)
    ).fetchone()[0]
    assert remaining == 1


def test_build_draft_sequence_complete(conn):
    lead = add_lead(conn, step=5)
    with pytest.raises(drafts.DraftError):
        drafts.build_draft(conn, lead)


def test_build_draft_missing_lead(conn):
    with pytest.raises(drafts.DraftError):
        drafts.build_draft(conn, 9999)


def test_build_draft_excludes_invalid_founders(conn):
    lead = add_lead(conn, company="Acme")
    add_founder(conn, lead, name="Nina", email="nina@acme.com")
    conn.execute(
        "INSERT INTO founders (lead_id, name, first_name, email, invalid) VALUES (?,?,?,?,1)",
        (lead, "Oleg", "Oleg", "oleg@acme.com"),
    )
    conn.commit()
    d = drafts.build_draft(conn, lead)
    assert d["to_addrs"] == "nina@acme.com"


def test_build_draft_no_reachable_recipients(conn):
    lead = add_lead(conn, company="Acme")
    conn.execute(
        "INSERT INTO founders (lead_id, name, first_name, email, invalid) VALUES (?,?,?,?,1)",
        (lead, "Oleg", "Oleg", "oleg@acme.com"),
    )
    conn.commit()
    with pytest.raises(drafts.DraftError):
        drafts.build_draft(conn, lead)


def test_followup_subject_prefixes_original(conn):
    lead = add_lead(conn, company="Acme")
    add_founder(conn, lead, email="jane@acme.com")
    # A sent step-0 message with a subject.
    conn.execute(
        "INSERT INTO messages (lead_id, step, to_addrs, subject, body, status) "
        "VALUES (?,0,?,?,?,'sent')",
        (lead, "jane@acme.com", "Hello there", "x"),
    )
    conn.execute("UPDATE leads SET step=1 WHERE id=?", (lead,))
    conn.commit()
    d = drafts.build_draft(conn, lead)
    assert d["subject"] == "Re: Hello there"

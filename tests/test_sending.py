import pytest

from app.services import sending
from tests.conftest import add_lead, add_message, set_setting


def _row(conn, mid):
    return conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()


def test_dry_run_marks_dry_run_and_bypasses_limits(conn):
    set_setting(conn, "daily_cap", "0")
    set_setting(conn, "send_window", "00:00-00:01")
    lead = add_lead(conn)
    mid = add_message(conn, lead)
    result = sending.send_message(conn, mid, dry_run=True)
    assert result["outcome"] == "dry_run"
    assert _row(conn, mid)["status"] == "dry_run"


def test_cap_skip_leaves_message_sendable(conn):
    set_setting(conn, "send_window", "")
    set_setting(conn, "daily_cap", "0")
    lead = add_lead(conn)
    mid = add_message(conn, lead)
    with pytest.raises(sending.SendSkipped) as exc:
        sending.send_message(conn, mid, dry_run=False)
    assert exc.value.kind == "cap"
    assert _row(conn, mid)["status"] == "draft"  # claim was released


def test_window_skip(conn, monkeypatch):
    set_setting(conn, "daily_cap", "25")
    monkeypatch.setattr(sending, "in_send_window", lambda *a, **k: False)
    lead = add_lead(conn)
    mid = add_message(conn, lead)
    with pytest.raises(sending.SendSkipped) as exc:
        sending.send_message(conn, mid, dry_run=False)
    assert exc.value.kind == "window"
    assert _row(conn, mid)["status"] == "draft"


def test_successful_send_advances_lead(conn, monkeypatch):
    set_setting(conn, "send_window", "")
    set_setting(conn, "daily_cap", "25")
    lead = add_lead(conn)
    mid = add_message(conn, lead)

    monkeypatch.setattr(
        sending.gmail_send, "send_email",
        lambda *a, **k: {"message_id": "<new@x>", "attached": True},
    )
    result = sending.send_message(conn, mid, dry_run=False)
    assert result["outcome"] == "sent"
    assert _row(conn, mid)["status"] == "sent"
    lead_row = conn.execute("SELECT * FROM leads WHERE id=?", (lead,)).fetchone()
    assert lead_row["status"] == "contacted"
    assert lead_row["step"] == 1


def test_ambiguous_failure_becomes_send_unknown_and_never_retried(conn, monkeypatch):
    set_setting(conn, "send_window", "")
    set_setting(conn, "daily_cap", "25")
    lead = add_lead(conn)
    mid = add_message(conn, lead)

    def boom(*a, **k):
        raise sending.gmail_send.DeliveryError("socket died", submitted=True)

    monkeypatch.setattr(sending.gmail_send, "send_email", boom)
    with pytest.raises(sending.SendError):
        sending.send_message(conn, mid, dry_run=False)
    assert _row(conn, mid)["status"] == "send_unknown"

    # A second attempt must refuse rather than risk a duplicate email.
    with pytest.raises(sending.SendError):
        sending.send_message(conn, mid, dry_run=False)


def test_hard_failure_is_retryable(conn, monkeypatch):
    set_setting(conn, "send_window", "")
    set_setting(conn, "daily_cap", "25")
    lead = add_lead(conn)
    mid = add_message(conn, lead)

    def refuse(*a, **k):
        raise sending.gmail_send.DeliveryError("recipient refused", submitted=False)

    monkeypatch.setattr(sending.gmail_send, "send_email", refuse)
    with pytest.raises(sending.SendError):
        sending.send_message(conn, mid, dry_run=False)
    assert _row(conn, mid)["status"] == "send_failed"

    # Now it can be sent once the underlying problem is fixed.
    monkeypatch.setattr(
        sending.gmail_send, "send_email",
        lambda *a, **k: {"message_id": "<ok@x>", "attached": False},
    )
    result = sending.send_message(conn, mid, dry_run=False)
    assert result["outcome"] == "sent"


def test_recover_stale_sending(conn):
    lead = add_lead(conn)
    mid = add_message(conn, lead, status="sending")
    assert sending.recover_stale_sending(conn) == 1
    assert _row(conn, mid)["status"] == "send_unknown"


def test_resolve_unknown_as_sent(conn):
    lead = add_lead(conn, step=1)
    mid = add_message(conn, lead, status="send_unknown", step=1)
    out = sending.resolve_message(conn, mid, was_sent=True)
    assert out["status"] == "sent"
    lead_row = conn.execute("SELECT * FROM leads WHERE id=?", (lead,)).fetchone()
    assert lead_row["status"] == "contacted"


def test_resolve_unknown_as_not_sent(conn):
    lead = add_lead(conn)
    mid = add_message(conn, lead, status="send_unknown")
    out = sending.resolve_message(conn, mid, was_sent=False)
    assert out["status"] == "send_failed"


def test_resolve_rejects_non_review_status(conn):
    lead = add_lead(conn)
    mid = add_message(conn, lead, status="draft")
    with pytest.raises(sending.SendError):
        sending.resolve_message(conn, mid, was_sent=True)


def test_list_review_returns_unknown_and_failed(conn):
    lead = add_lead(conn)
    add_message(conn, lead, status="send_unknown")
    add_message(conn, lead, status="send_failed")
    add_message(conn, lead, status="sent")
    statuses = {r["status"] for r in sending.list_review(conn)}
    assert statuses == {"send_unknown", "send_failed"}

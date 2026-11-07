"""Shared send orchestration and message state transitions.

Every send — individual or bulk — goes through `send_message`. It claims the
message atomically, verifies eligibility, enforces the daily cap and send
window, and transitions the message through an explicit lifecycle:

    draft / dry_run / send_failed  --claim-->  sending
      sending --(dry run)--> dry_run
      sending --(accepted)--> sent              (lead sequence advances)
      sending --(hard failure)--> send_failed   (deliberate retry allowed)
      sending --(ambiguous)--> send_unknown     (never auto-retried)

SMTP acceptance and SQLite writes cannot be made atomic together, so an
ambiguous outcome is recorded as `send_unknown` and left for manual
reconciliation rather than being retried blindly. There is no such thing as
guaranteed exactly-once delivery over SMTP; this module makes the *common*
failure modes safe and never silently duplicates a message.
"""
import sqlite3
import time
from datetime import datetime, timedelta

from .. import gmail_read, gmail_send
from ..db import get_conn
from ..util import clean_header, in_send_window, now_iso, now_iso_precise, validate_recipients
from . import settings as settings_svc

# Statuses a message may be sent from.
SENDABLE = ("draft", "dry_run", "send_failed")
# Statuses that require the user to reconcile before anything else happens.
REVIEW_STATUSES = ("send_unknown", "send_failed")


class SendError(Exception):
    def __init__(self, message, *, ambiguous=False, status=None):
        super().__init__(message)
        self.ambiguous = ambiguous
        self.status = status


class SendSkipped(SendError):
    """A reversible skip: the message is left sendable and not marked failed."""

    def __init__(self, reason, kind):
        super().__init__(reason)
        self.kind = kind  # cap | window | interval | lead


def _safe_error(exc):
    """A short, credential-free description of a failure for storage/logs."""
    text = str(exc).replace("\n", " ").strip()
    return f"{type(exc).__name__}: {text[:200]}"


def _next_step(lead_step, message_step):
    """New sequence cursor after a successful send.

    A lead never moves backwards, and never re-sends the same step
    automatically: the cursor becomes ``max(current, message_step + 1)`` capped
    at 5. For an automatic send (message_step == current) this is the familiar
    ``current + 1``; for a manual send of an earlier/later template it keeps the
    cursor sensible without skipping or rewinding the sequence.
    """
    cur = lead_step or 0
    return min(max(cur, (message_step or 0) + 1), 5)


def _get(conn, message_id):
    return conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()


# ---------- quota accounting ----------

def count_sent_today(conn):
    """Successfully sent messages for the current *local* calendar day."""
    return conn.execute(
        "SELECT COUNT(*) FROM messages "
        "WHERE status='sent' AND date(sent_at)=date('now','localtime')"
    ).fetchone()[0]


def reserved_today(conn):
    """Today's real-send quota consumption: delivered *and* in-flight.

    Counting in-flight (`sending`) and unresolved (`send_unknown`) attempts means
    concurrent senders cannot collectively exceed the cap, and an ambiguous
    attempt conservatively consumes a slot until it is reconciled.
    """
    return conn.execute(
        "SELECT COUNT(*) FROM messages "
        "WHERE status IN ('sent','sending','send_unknown') "
        "AND date(COALESCE(sent_at, last_attempt_at))=date('now','localtime')"
    ).fetchone()[0]


def claim(conn, message_id, enforce_cap=False):
    """Atomically move a sendable message into 'sending'.

    The eligibility check, the daily-cap check, and the write are a single
    SQLite statement, so concurrent callers (a double click, a duplicate
    enqueue, or two threads) can never both win the same claim, and never
    collectively pass the cap. The cap subquery counts the committed send
    state, so N concurrent claims admit at most `cap` winners.
    """
    if enforce_cap:
        cap = settings_svc.get_setting_int("daily_cap", 40)
        cur = conn.execute(
            "UPDATE messages SET status='sending', last_attempt_at=?, "
            "send_attempts=COALESCE(send_attempts,0)+1 "
            "WHERE id=? AND status IN ('draft','dry_run','send_failed') "
            "AND (SELECT COUNT(*) FROM messages "
            "     WHERE status IN ('sent','sending','send_unknown') "
            "       AND date(COALESCE(sent_at, last_attempt_at))=date('now','localtime')) < ?",
            (now_iso_precise(), message_id, cap),
        )
    else:
        cur = conn.execute(
            "UPDATE messages SET status='sending', last_attempt_at=?, "
            "send_attempts=COALESCE(send_attempts,0)+1 "
            "WHERE id=? AND status IN ('draft','dry_run','send_failed')",
            (now_iso_precise(), message_id),
        )
    conn.commit()
    return cur.rowcount == 1


def check_limits(conn, when=None):
    """Return (reason, kind) when a real send is not allowed right now, else None.

    The daily cap is *not* checked here — it is enforced atomically inside
    `claim` so concurrent senders cannot race past it. This only covers the
    time-based window.
    """
    window = settings_svc.get_setting("send_window", "", conn=conn)
    if not in_send_window(window, when):
        return f"outside send window ({window})", "window"
    return None


# ---------- minimum interval between consecutive sends ----------

def seconds_until_next_send(conn, now=None):
    """Seconds to wait before the next real send is allowed by the interval.

    Paced from the *start* of the previous attempt (`last_attempt_at`, written
    when the message is claimed) rather than its completion, so consecutive
    sends are spaced by exactly the interval no matter how long one SMTP
    transaction takes. `min_send_interval_seconds` may be fractional
    (e.g. ``1`` = one per second, ``0.5`` = two per second).
    """
    interval = settings_svc.get_setting_float("min_send_interval_seconds", 2.0)
    if interval <= 0:
        return 0.0
    last = conn.execute(
        "SELECT MAX(COALESCE(last_attempt_at, sent_at)) FROM messages "
        "WHERE status IN ('sent','sending','send_unknown','send_failed')"
    ).fetchone()[0]
    if not last:
        return 0.0
    try:
        last_dt = datetime.fromisoformat(last)
    except (TypeError, ValueError):
        return 0.0
    now = now or datetime.now()
    return max(0.0, interval - (now - last_dt).total_seconds())


def wait_for_interval(conn=None, sleep=True, now=None):
    """Pace sequential sends. Returns the computed wait in seconds.

    Called by the single sender worker before each real submission so a large
    batch is throttled. `sleep=False` is used by tests and dry runs.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        wait = seconds_until_next_send(conn, now)
    finally:
        if own:
            conn.close()
    if wait and sleep:
        time.sleep(wait)
    return wait


# ---------- failure handling ----------

def _mark_failure(conn, message_id, err):
    status = "send_unknown" if err.ambiguous else "send_failed"
    with conn:
        conn.execute(
            "UPDATE messages SET status=?, last_error=?, last_attempt_at=? WHERE id=?",
            (status, _safe_error(err), now_iso_precise(), message_id),
        )
    return status


def send_message(conn, message_id, dry_run, session=None):
    """Send (or simulate) one message. Raises SendError/SendSkipped on failure.

    ``session`` is an optional :class:`gmail_send.SmtpSession` shared across a
    sequential batch so the SMTP connection and login are reused. When omitted,
    the send opens and closes its own one-shot connection.
    """
    row = _get(conn, message_id)
    if not row:
        raise SendError("message not found", status=404)
    status = row["status"]
    if status == "sent":
        raise SendError("already sent", status=400)
    if status == "send_unknown":
        raise SendError("previous attempt is unresolved; reconcile before retrying", status=409)
    if status == "sending":
        raise SendError("message is already being processed", status=409)
    if status not in SENDABLE:
        raise SendError(f"cannot send a '{status}' message", status=409)

    if not claim(conn, message_id, enforce_cap=not dry_run):
        # Either the cap blocked it (message is still sendable) or another
        # caller won the claim first (message is now sending/sent).
        current = _get(conn, message_id)
        if current and current["status"] in SENDABLE:
            raise SendSkipped("daily cap reached", "cap")
        raise SendError("already sent or currently being processed", status=409)

    try:
        return _perform(conn, row, dry_run, session)
    except SendSkipped:
        # Undo the claim; the message stays in its original sendable state.
        with conn:
            conn.execute("UPDATE messages SET status=? WHERE id=?", (row["status"], message_id))
        raise
    except SendError as e:
        _mark_failure(conn, message_id, e)
        raise
    except Exception as e:  # unexpected -> ambiguous, never auto-retried
        err = SendError(_safe_error(e), ambiguous=True)
        _mark_failure(conn, message_id, err)
        raise err from e


def _perform(conn, row, dry_run, session=None):
    message_id = row["id"]
    lead = conn.execute("SELECT * FROM leads WHERE id=?", (row["lead_id"],)).fetchone()
    if not lead:
        raise SendError("lead not found", status=404)
    if (lead["status"] or "") in ("replied", "bounced", "done"):
        raise SendSkipped(f"lead marked '{lead['status']}'; not sending", "lead")

    valid, invalid = validate_recipients(row["to_addrs"])
    if not valid:
        raise SendError("no valid recipient email address")
    if invalid:
        raise SendError("invalid recipient address(es): " + ", ".join(invalid[:3]))

    if dry_run:
        with conn:
            conn.execute(
                "UPDATE messages SET status='dry_run', last_error=NULL WHERE id=?", (message_id,)
            )
        return {"dry_run": True, "to": valid, "subject": row["subject"], "outcome": "dry_run"}

    limit = check_limits(conn)
    if limit:
        reason, kind = limit
        raise SendSkipped(reason, kind)

    # Application-level guard against re-sending the same sequence step. The
    # partial unique index idx_messages_sent_step is the database backstop.
    # Manually-sent messages (a user-chosen template) are exempt so a template
    # can be sent to a lead at any time.
    if not row["manual"]:
        dup = conn.execute(
            "SELECT id FROM messages WHERE lead_id=? AND step IS ? AND status='sent' AND id<>?",
            (lead["id"], row["step"], message_id),
        ).fetchone()
        if dup:
            raise SendError("a message for this sequence step was already sent")

    original = None
    if row["step"] and row["step"] > 0:
        original = conn.execute(
            "SELECT message_id FROM messages WHERE lead_id=? AND step=0 AND status='sent' ORDER BY id LIMIT 1",
            (row["lead_id"],),
        ).fetchone()

    resume_path = settings_svc.get_setting("resume_path", "", conn=conn) if row["attach_resume"] else None
    sig = settings_svc.get_setting("signature", "", conn=conn)

    # Persist the intended Message-ID *before* the SMTP attempt so an
    # interrupted send can later be found in Gmail Sent Mail by this id.
    client_mid = row["client_message_id"] or gmail_send.new_message_id()
    with conn:
        conn.execute(
            "UPDATE messages SET client_message_id=? WHERE id=?", (client_mid, message_id)
        )

    try:
        result = gmail_send.send_email(
            valid,
            clean_header(row["subject"]),
            row["body"],
            resume_path=resume_path,
            in_reply_to=original["message_id"] if original else None,
            signature=sig or None,
            message_id=client_mid,
            session=session,
        )
    except gmail_send.DeliveryError as e:
        raise SendError(_safe_error(e), ambiguous=e.submitted) from e

    gap = settings_svc.get_setting_int("step_gap_days", 2)
    ts = now_iso()
    next_due = (datetime.now() + timedelta(days=gap)).isoformat(timespec="seconds")
    # Message status and lead state advance together or not at all.
    try:
        with conn:
            conn.execute(
                "UPDATE messages SET status='sent', message_id=?, client_message_id=?, "
                "sent_at=?, last_error=NULL WHERE id=?",
                (result["message_id"], client_mid, ts, message_id),
            )
            conn.execute(
                "UPDATE leads SET step=?, last_sent_at=?, next_due_at=?, status='contacted' WHERE id=?",
                (_next_step(lead["step"], row["step"]), ts, next_due, lead["id"]),
            )
    except sqlite3.IntegrityError as e:
        # The unique step index fired: the message reached Gmail but cannot be
        # recorded as a distinct send. Treat as ambiguous, never resend.
        err = SendError(f"delivered but not recorded ({e})", ambiguous=True)
        _mark_failure(conn, message_id, err)
        raise err from e
    return {"sent": True, "to": valid, "attached": result["attached"], "outcome": "sent"}


def recover_stale_sending(conn):
    """On startup, move interrupted 'sending' messages to manual review.

    A record stuck in 'sending' means the process died mid-attempt; we cannot
    know whether Gmail accepted it, so we never return it to the draft queue.
    """
    with conn:
        cur = conn.execute(
            "UPDATE messages SET status='send_unknown', last_attempt_at=?, last_error=? "
            "WHERE status='sending'",
            (
                now_iso(),
                "Recovered from an interrupted send on startup; verify in Gmail Sent Mail",
            ),
        )
    return cur.rowcount


def list_review(conn):
    """Messages awaiting manual review (send_unknown / send_failed)."""
    rows = conn.execute(
        """SELECT m.*, l.company, l.status AS lead_status
           FROM messages m LEFT JOIN leads l ON l.id = m.lead_id
           WHERE m.status IN ('send_unknown','send_failed')
           ORDER BY m.last_attempt_at DESC, m.id DESC LIMIT 200"""
    ).fetchall()
    return [dict(r) for r in rows]


def reconcile_unknown(conn, message_id):
    """Check Gmail Sent Mail for an ambiguous message using its stable id.

    Returns ``{"found": bool, "reconciled": bool, "message": {...}}``. When the
    message is found it is marked sent. When it cannot be found *or* the check
    cannot be performed (no credentials / network), it stays `send_unknown` and
    the caller must obtain explicit manual confirmation before any retry.
    """
    row = _get(conn, message_id)
    if not row:
        raise SendError("message not found", status=404)
    if row["status"] not in REVIEW_STATUSES:
        raise SendError("message is not awaiting review", status=409)

    found = False
    if row["status"] == "send_unknown" and row["client_message_id"]:
        try:
            found = gmail_read.search_sent_by_message_id(row["client_message_id"])
        except Exception:
            found = False  # cannot establish confidently

    if found:
        return {"found": True, "reconciled": True,
                "message": resolve_message(conn, message_id, True)}
    return {"found": False, "reconciled": False, "message": dict(row)}


def resolve_message(conn, message_id, was_sent):
    """Reconcile a reviewed message after the user checked Gmail Sent Mail."""
    row = _get(conn, message_id)
    if not row:
        raise SendError("message not found", status=404)
    if row["status"] not in REVIEW_STATUSES:
        raise SendError("message is not awaiting review", status=409)

    if was_sent:
        lead = conn.execute("SELECT * FROM leads WHERE id=?", (row["lead_id"],)).fetchone()
        ts = row["sent_at"] or now_iso()
        gap = settings_svc.get_setting_int("step_gap_days", 2)
        next_due = (datetime.now() + timedelta(days=gap)).isoformat(timespec="seconds")
        with conn:
            conn.execute(
                "UPDATE messages SET status='sent', sent_at=?, last_error=NULL WHERE id=?",
                (ts, message_id),
            )
            if lead and (lead["status"] or "") not in ("replied", "bounced", "done"):
                conn.execute(
                    "UPDATE leads SET step=?, last_sent_at=?, next_due_at=?, status='contacted' WHERE id=?",
                    (_next_step(lead["step"], row["step"]), ts, next_due, lead["id"]),
                )
    else:
        # Confirmed not sent: make it retryable, do not silently reset to draft.
        with conn:
            conn.execute(
                "UPDATE messages SET status='send_failed', last_error=? WHERE id=?",
                ("User confirmed it was not sent; safe to retry", message_id),
            )
    return dict(_get(conn, message_id))

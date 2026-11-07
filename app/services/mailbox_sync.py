"""Match mailbox messages to leads and sync them into the database.

Matching is deliberately conservative:

1. exact Gmail thread id (strongest signal — the message shares our thread),
2. exact sender/recipient email address,
3. domain — only when exactly one lead matches.

If a rule produces more than one candidate the event is left *unmatched* and
recorded for review; a lead's sequence is never stopped on an ambiguous match.
Reply dedup uses the stable RFC Message-ID.
"""
import os
from datetime import datetime, timedelta

from .. import gmail_read
from ..util import now_iso
from . import settings as settings_svc


def _norm(addr):
    return (addr or "").strip().lower()


def _domain(addr):
    addr = _norm(addr)
    return addr.split("@", 1)[1] if "@" in addr else ""


def lead_id_for_email(conn, addr):
    addr = _norm(addr)
    if not addr or "@" not in addr:
        return None
    row = conn.execute(
        "SELECT lead_id FROM founders WHERE LOWER(email)=? LIMIT 1", (addr,)
    ).fetchone()
    return row["lead_id"] if row else None


def lead_ids_for_domain(conn, domain):
    domain = _norm(domain)
    if not domain:
        return []
    ids = set()
    for r in conn.execute("SELECT id FROM leads WHERE LOWER(domain)=?", (domain,)):
        ids.add(r["id"])
    for r in conn.execute(
        "SELECT DISTINCT lead_id FROM founders WHERE LOWER(email) LIKE ?",
        (f"%@{domain}",),
    ):
        ids.add(r["lead_id"])
    return sorted(i for i in ids if i is not None)


def _decide(ids):
    uniq = {i for i in ids if i is not None}
    if len(uniq) == 1:
        return next(iter(uniq)), False
    if len(uniq) > 1:
        return None, True
    return None, False


def match_lead(conn, msg, mode, extra_addrs=()):
    """Return {'lead_id', 'method', 'ambiguous', 'note'} for a message."""
    # 1. thread id
    if msg.get("thread_id"):
        ids = [
            r["lead_id"]
            for r in conn.execute(
                "SELECT DISTINCT lead_id FROM messages WHERE thread_id=?",
                (msg["thread_id"],),
            ).fetchall()
        ]
        lead_id, ambiguous = _decide(ids)
        if lead_id is not None:
            return {"lead_id": lead_id, "method": "thread", "ambiguous": False, "note": None}
        if ambiguous:
            return {"lead_id": None, "method": "thread", "ambiguous": True,
                    "note": "multiple leads share this thread"}

    if mode == "sent":
        addrs = list(msg.get("to", [])) + list(msg.get("cc", []))
    else:
        addrs = ([msg.get("from")] if msg.get("from") else []) + list(extra_addrs)

    # 2. exact email address
    ids = [lead_id_for_email(conn, a) for a in addrs]
    lead_id, ambiguous = _decide(ids)
    if lead_id is not None:
        return {"lead_id": lead_id, "method": "email", "ambiguous": False, "note": None}
    if ambiguous:
        return {"lead_id": None, "method": "email", "ambiguous": True,
                "note": "sender address matches multiple leads"}

    # 3. domain, only if unambiguous
    ids = []
    for dom in {_domain(a) for a in addrs if _domain(a)}:
        ids.extend(lead_ids_for_domain(conn, dom))
    lead_id, ambiguous = _decide(ids)
    if lead_id is not None:
        return {"lead_id": lead_id, "method": "domain", "ambiguous": False, "note": None}
    if ambiguous:
        return {"lead_id": None, "method": "domain", "ambiguous": True,
                "note": "domain matches multiple leads"}

    return {"lead_id": None, "method": None, "ambiguous": False, "note": None}


def classify(msg):
    subject = (msg.get("subject") or "").lower()
    sender = _norm(msg.get("from"))
    auto = (msg.get("auto_submitted") or "").lower()
    if (
        "mailer-daemon" in sender
        or "maildaemon" in sender
        or "undeliverable" in subject
        or "delivery status" in subject
        or "delivery failure" in subject
        or "failure notice" in subject
    ):
        return "bounce"
    if (
        "auto-submitted" in auto
        or auto.startswith("auto-")
        or "out of office" in subject
        or "automatic reply" in subject
        or "autoreply" in subject
        or "auto-reply" in subject
    ):
        return "ooo"
    return "reply"


def _recompute_lead(conn, lead_id):
    gap = settings_svc.get_setting_int("step_gap_days", 2)
    msgs = conn.execute(
        "SELECT * FROM messages WHERE lead_id=? AND status='sent' ORDER BY sent_at",
        (lead_id,),
    ).fetchall()
    count = len(msgs)
    last = msgs[-1]["sent_at"] if msgs else None
    next_due = None
    if last:
        try:
            next_due = (datetime.fromisoformat(last) + timedelta(days=gap)).isoformat(timespec="seconds")
        except Exception:
            next_due = None
    status = "contacted"
    cur = conn.execute("SELECT status FROM leads WHERE id=?", (lead_id,)).fetchone()
    if cur and cur["status"] in ("replied", "bounced"):
        status = cur["status"]
    conn.execute(
        "UPDATE leads SET step=?, last_sent_at=?, next_due_at=?, status=? WHERE id=?",
        (min(count, 5), last, next_due, status, lead_id),
    )


def sync_sent(conn, limit=50):
    """Match Gmail Sent Mail to leads and record unseen sent messages."""
    messages = gmail_read.fetch_sent(limit)
    new_messages = 0
    unmatched = 0
    ambiguous = 0
    touched = set()
    for m in messages:
        if not m.get("message_id"):
            continue
        match = match_lead(conn, m, "sent")
        if match["ambiguous"]:
            ambiguous += 1
            continue
        lead_id = match["lead_id"]
        if not lead_id:
            unmatched += 1
            continue
        exists = conn.execute(
            "SELECT id FROM messages WHERE message_id=?", (m["message_id"],)
        ).fetchone()
        if not exists:
            conn.execute(
                """INSERT OR IGNORE INTO messages
                   (lead_id, step, to_addrs, subject, body, status, message_id, thread_id, sent_at, attach_resume)
                   VALUES (?,?,?,?,?, 'sent', ?,?,?,1)""",
                (lead_id, 0, ", ".join(m["to"]), m["subject"], "", m["message_id"],
                 m["thread_id"], m["date"]),
            )
            new_messages += 1
        touched.add(lead_id)
    with conn:
        for lead_id in touched:
            _recompute_lead(conn, lead_id)
    return {
        "scanned": len(messages),
        "new_messages": new_messages,
        "unmatched": unmatched,
        "ambiguous": ambiguous,
    }


def sync_replies(conn, limit=50):
    """Match INBOX mail to leads, classify it, and record replies/bounces/OOO."""
    inbox = gmail_read.fetch_inbox(limit)
    me = _norm(os.getenv("GMAIL_ADDRESS"))
    findings = 0
    unmatched = 0
    ambiguous = 0
    skipped = 0
    for m in inbox:
        if me and _norm(m.get("from")) == me:
            continue
        if not m.get("message_id"):
            continue
        kind = classify(m)
        extra = gmail_read.find_emails(m.get("body")) if kind == "bounce" else []

        # dedup on the stable RFC Message-ID
        dup = conn.execute(
            "SELECT id FROM replies WHERE message_id=?", (m["message_id"],)
        ).fetchone()
        if dup:
            continue

        match = match_lead(conn, m, "reply", extra_addrs=extra)
        lead_id = match["lead_id"]

        if match["ambiguous"]:
            ambiguous += 1
            with conn:
                conn.execute(
                    """INSERT OR IGNORE INTO replies
                       (lead_id, thread_id, from_addr, message_id, snippet, received_at, kind, company, match_note)
                       VALUES (NULL,?,?,?,?,?,?,NULL,?)""",
                    (m["thread_id"], _norm(m.get("from")), m["message_id"],
                     gmail_read.snippet(m["body"]), m["date"], kind, match["note"]),
                )
            continue

        if not lead_id:
            # not part of any outreach thread -> ignore
            skipped += 1
            continue

        lc = conn.execute("SELECT company FROM leads WHERE id=?", (lead_id,)).fetchone()
        company = lc["company"] if lc else None
        with conn:
            conn.execute(
                """INSERT OR IGNORE INTO replies
                   (lead_id, thread_id, from_addr, message_id, snippet, received_at, kind, company, match_note)
                   VALUES (?,?,?,?,?,?,?,?,NULL)""",
                (lead_id, m["thread_id"], _norm(m.get("from")), m["message_id"],
                 gmail_read.snippet(m["body"]), m["date"], kind, company),
            )
            if kind == "reply":
                conn.execute(
                    "UPDATE leads SET status='replied', next_due_at=NULL WHERE id=?",
                    (lead_id,),
                )
            elif kind == "bounce":
                conn.execute(
                    "UPDATE leads SET status='bounced', next_due_at=NULL WHERE id=?",
                    (lead_id,),
                )
            # ooo: recorded for visibility, sequence is not stopped
        findings += 1

    return {
        "scanned": len(inbox),
        "findings": findings,
        "unmatched": unmatched,
        "ambiguous": ambiguous,
        "ignored": skipped,
    }


def sync_all(conn, limit=50):
    return {"sent": sync_sent(conn, limit), "replies": sync_replies(conn, limit)}

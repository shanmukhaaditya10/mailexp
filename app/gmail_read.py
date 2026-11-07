import os
import email
import re
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime

from imapclient import IMAPClient

SENT_FOLDER = "[Gmail]/Sent Mail"


def connect():
    host = os.getenv("IMAP_HOST", "imap.gmail.com")
    user = os.getenv("GMAIL_ADDRESS")
    pwd = os.getenv("GMAIL_APP_PASSWORD")
    if not user or not pwd:
        raise RuntimeError("GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set in .env")
    client = IMAPClient(host, ssl=True, timeout=60)
    client.login(user, pwd)
    return client


def _dec(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _addrs(header_value):
    out = []
    if not header_value:
        return out
    for part in re.split(r",", header_value):
        _, addr = parseaddr(part)
        if addr and "@" in addr:
            out.append(addr.lower())
    return out


def _domain(addr):
    return addr.split("@", 1)[1] if "@" in addr else ""


def _parse(raw):
    msg = email.message_from_bytes(raw)
    to = _addrs(_dec(msg.get("To")))
    cc = _addrs(_dec(msg.get("Cc")))
    from_addr = parseaddr(_dec(msg.get("From")))[1].lower()
    try:
        date = parsedate_to_datetime(msg.get("Date")).isoformat()
    except Exception:
        date = ""
    return {
        "message_id": (msg.get("Message-ID") or "").strip(),
        "in_reply_to": (msg.get("In-Reply-To") or "").strip(),
        "references": (msg.get("References") or "").strip(),
        "subject": _dec(msg.get("Subject")),
        "from": from_addr,
        "to": to,
        "cc": cc,
        "date": date,
        "auto_submitted": (msg.get("Auto-Submitted") or "").strip(),
        "body": _body(msg),
    }


def _body(msg):
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        return payload.decode(part.get_content_charset() or "utf-8", "replace")
            return ""
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode(msg.get_content_charset() or "utf-8", "replace")
    except Exception:
        pass
    return ""


def fetch_sent(limit=50):
    client = connect()
    try:
        client.select_folder(SENT_FOLDER, readonly=True)
        uids = client.search(["ALL"])
        uids = uids[-limit:]
        if not uids:
            return []
        data = client.fetch(uids, ["RFC822", "X-GM-THRID"])
        out = []
        for uid, meta in data.items():
            parsed = _parse(meta[b"RFC822"])
            parsed["thread_id"] = str(meta.get(b"X-GM-THRID", ""))
            out.append(parsed)
        return out
    finally:
        client.logout()


def fetch_inbox(limit=50):
    client = connect()
    try:
        client.select_folder("INBOX", readonly=True)
        uids = client.search(["ALL"])
        uids = uids[-limit:]
        if not uids:
            return []
        data = client.fetch(uids, ["RFC822", "X-GM-THRID"])
        out = []
        for uid, meta in data.items():
            parsed = _parse(meta[b"RFC822"])
            parsed["thread_id"] = str(meta.get(b"X-GM-THRID", ""))
            out.append(parsed)
        return out
    finally:
        client.logout()


def search_sent_by_message_id(message_id):
    """Return True if a message with this Message-ID exists in Gmail Sent Mail.

    Raises on connection/auth failure so the caller can treat the result as
    "cannot establish" rather than "not sent".
    """
    if not message_id:
        return False
    client = connect()
    try:
        client.select_folder(SENT_FOLDER, readonly=True)
        return bool(client.search(["HEADER", "Message-ID", message_id]))
    finally:
        client.logout()


def snippet(text, n=200):
    text = re.sub(r"\s+", " ", (text or "")).strip()
    return text[:n]


_EMAIL_IN_TEXT = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def find_emails(text, limit=5):
    """Best-effort extraction of addresses from a body (e.g. bounce reports)."""
    if not text:
        return []
    seen = []
    for addr in _EMAIL_IN_TEXT.findall(text):
        low = addr.lower()
        if low not in seen:
            seen.append(low)
        if len(seen) >= limit:
            break
    return seen

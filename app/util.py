"""Small, dependency-free helpers shared across services and routes."""
import re
from datetime import datetime

# Pragmatic email check: a local part, an @, a domain with at least one dot and
# a 2+ char TLD. Deliberately not RFC-5322-complete; it rejects the obvious
# junk and header-injection attempts without rejecting normal addresses.
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# Any CR/LF (or other C0 control chars) in a header value is a header-injection
# vector; strip them before use.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def now_iso_precise():
    """Sub-second timestamp for send pacing (interval can be < 1s)."""
    return datetime.now().isoformat(timespec="microseconds")


def clean_header(value):
    """Remove control characters that could inject extra mail headers."""
    if value is None:
        return ""
    return _CONTROL_RE.sub("", str(value)).strip()


def is_valid_email(addr):
    if not addr:
        return False
    addr = addr.strip()
    if _CONTROL_RE.search(addr):
        return False
    return bool(EMAIL_RE.match(addr))


def split_recipients(to_addrs):
    """Split a comma/semicolon separated recipient string into clean addresses."""
    if not to_addrs:
        return []
    parts = re.split(r"[,;]", str(to_addrs))
    return [clean_header(p) for p in (part.strip() for part in parts) if p]


def validate_recipients(to_addrs):
    """Return (valid, invalid) lists of recipient addresses."""
    valid, invalid = [], []
    for addr in split_recipients(to_addrs):
        (valid if is_valid_email(addr) else invalid).append(addr)
    return valid, invalid


def parse_send_window(window):
    """Parse a 'HH:MM-HH:MM' window into (start_minute, end_minute).

    Returns None when the value is empty or malformed (meaning "no window").
    """
    if not window or "-" not in str(window):
        return None
    start, _, end = str(window).partition("-")

    def to_minutes(text):
        hh, _, mm = text.strip().partition(":")
        h = int(hh)
        m = int(mm) if mm else 0
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError("out of range")
        return h * 60 + m

    try:
        return to_minutes(start), to_minutes(end)
    except (ValueError, TypeError):
        return None


def in_send_window(window, when=None):
    """True if `when` (default: now) falls inside the configured send window."""
    parsed = parse_send_window(window)
    if parsed is None:
        return True
    start, end = parsed
    when = when or datetime.now()
    minute = when.hour * 60 + when.minute
    if start == end:
        return True
    if start < end:
        return start <= minute < end
    # window wraps past midnight, e.g. 22:00-06:00
    return minute >= start or minute < end

import os
import socket
import smtplib
import mimetypes
from email.message import EmailMessage
from email.utils import make_msgid, formatdate

import markdown as md

from .sanitize import sanitize_email_html

MD_EXTENSIONS = ["nl2br", "sane_lists", "tables"]

# SMTP replies that mean the server definitively did NOT accept the message, so
# a retry is safe. Anything else raised during the send is treated as ambiguous.
_REJECTED = (
    smtplib.SMTPRecipientsRefused,
    smtplib.SMTPSenderRefused,
    smtplib.SMTPDataError,
    smtplib.SMTPNotSupportedError,
)


class DeliveryError(Exception):
    """Raised when a send fails.

    `submitted=True` means the message may have reached the server (the SMTP
    transaction was interrupted after we handed data to the socket), so the
    caller must NOT retry automatically. `submitted=False` means the failure is
    known to have happened before the message was accepted.
    """

    def __init__(self, message, submitted):
        super().__init__(message)
        self.submitted = submitted


def render_html(text):
    return sanitize_email_html(md.markdown(text or "", extensions=MD_EXTENSIONS))


def new_message_id():
    """Generate the RFC 5322 Message-ID we will use for an outgoing message.

    Callers persist this *before* the SMTP attempt so an interrupted send can
    later be reconciled against Gmail Sent Mail by its stable identifier.

    The domain is supplied explicitly (from the Gmail address, else a constant)
    so `make_msgid` never performs a blocking `socket.getfqdn()` lookup; Gmail
    rewrites the Message-ID on send regardless.
    """
    addr = os.getenv("GMAIL_ADDRESS") or ""
    domain = addr.split("@", 1)[1] if "@" in addr else "mailexp.local"
    return make_msgid(domain=domain)


def _attach_resume(msg, path):
    if not path or not os.path.exists(path):
        return False
    ctype, _ = mimetypes.guess_type(path)
    maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
    with open(path, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype=maintype,
            subtype=subtype,
            filename=os.path.basename(path),
        )
    return True


def build_email(to_addrs, subject, body, resume_path=None,
                in_reply_to=None, references=None, signature=None, message_id=None):
    """Build the outgoing EmailMessage without any network I/O.

    Returns ``(msg, message_id, attached)`` so a caller can prepare a message
    once and hand it to a shared :class:`SmtpSession`.
    """
    user = os.getenv("GMAIL_ADDRESS") or ""
    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = ", ".join(to_addrs)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    message_id = message_id or new_message_id()
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    elif in_reply_to:
        msg["References"] = in_reply_to

    text = body
    if signature:
        text = f"{body}\n\n{signature}"
    # Plain-text fallback keeps the raw markdown; the HTML part renders it.
    msg.set_content(text)
    msg.add_alternative(render_html(text), subtype="html")
    attached = _attach_resume(msg, resume_path)
    return msg, message_id, attached


class SmtpSession:
    """A reusable, lazily-connected authenticated SMTP_SSL connection.

    One session is shared across every real send in a single sequential batch,
    so the TCP + TLS + AUTH cost is paid once instead of per email. The socket
    is opened on first use, and re-opened on the next send after any failure
    that leaves the connection state unknown.

    A message whose delivery outcome is *ambiguous* (a timeout or disconnect
    mid-transaction) is never retried by this session: the caller records it for
    reconciliation. Only the connection is dropped; the failed message is not
    re-sent.
    """

    HOST = "smtp.gmail.com"
    PORT = 465

    def __init__(self, host=None, port=None, timeout=60):
        self.host = host or self.HOST
        self.port = port or self.PORT
        self.timeout = timeout
        self._server = None

    @property
    def connected(self):
        return self._server is not None

    def _connect(self):
        user = os.getenv("GMAIL_ADDRESS")
        pwd = os.getenv("GMAIL_APP_PASSWORD")
        if not user or not pwd:
            raise DeliveryError(
                "GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set in .env", submitted=False
            )
        try:
            # Connection / auth failures happen before submission.
            server = smtplib.SMTP_SSL(
                self.host, self.port, timeout=self.timeout, local_hostname=socket.gethostname()
            )
            server.login(user, pwd)
        except _REJECTED as e:
            raise DeliveryError(str(e), submitted=False) from e
        except Exception as e:
            raise DeliveryError(str(e), submitted=False) from e
        self._server = server
        return server

    def _ensure(self):
        if self._server is None:
            return self._connect()
        return self._server

    def reset(self):
        """Drop the current connection (best-effort QUIT), if any."""
        server, self._server = self._server, None
        if server is not None:
            try:
                server.quit()
            except Exception:
                pass

    def send(self, msg):
        """Send one prepared message over the (shared) connection."""
        server = self._ensure()
        try:
            server.send_message(msg)
        except _REJECTED as e:
            # Server explicitly rejected the message; it was not delivered.
            raise DeliveryError(str(e), submitted=False) from e
        except Exception as e:
            # Timeout / disconnect mid-transaction: delivery is unknown and the
            # connection can no longer be trusted. Drop it so the *next* message
            # reconnects; the message that failed is never auto-retried.
            self.reset()
            raise DeliveryError(str(e), submitted=True) from e

    def close(self):
        self.reset()


def send_email(to_addrs, subject, body, resume_path=None,
               in_reply_to=None, references=None, signature=None, message_id=None,
               session=None):
    """Build and send a single email.

    When ``session`` is supplied the message is sent over it and the connection
    is left open for the caller to reuse; otherwise a one-shot session is opened
    and closed here. Raises :class:`DeliveryError` on failure.
    """
    user = os.getenv("GMAIL_ADDRESS")
    pwd = os.getenv("GMAIL_APP_PASSWORD")
    if not user or not pwd:
        raise DeliveryError("GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set in .env", submitted=False)

    msg, message_id, attached = build_email(
        to_addrs, subject, body, resume_path=resume_path,
        in_reply_to=in_reply_to, references=references,
        signature=signature, message_id=message_id,
    )
    own = session is None
    session = session or SmtpSession()
    try:
        session.send(msg)
    finally:
        if own:
            session.close()
    return {"message_id": message_id, "attached": attached}

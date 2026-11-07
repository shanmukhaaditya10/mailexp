"""Draft generation and template preparation."""
from .. import render


class DraftError(Exception):
    """Raised when a draft cannot be built (maps to an HTTP 4xx in routes)."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def next_template(conn, step):
    return conn.execute(
        "SELECT * FROM templates WHERE step=? ORDER BY id LIMIT 1", (step,)
    ).fetchone()


def original_subject(conn, lead_id):
    row = conn.execute(
        "SELECT subject FROM messages WHERE lead_id=? AND step=0 ORDER BY id LIMIT 1",
        (lead_id,),
    ).fetchone()
    return row["subject"] if row else None


def build_draft(conn, lead_id, template_id=None, manual=None):
    """Create (replacing any existing draft) the next message for a lead.

    With no `template_id`, the template for the lead's current sequence step is
    used. With a `template_id`, that exact template is used — letting the caller
    send a template they chose rather than the automatic next step.

    A message built from an explicitly chosen `template_id` is marked
    ``manual``: it is exempt from the one-per-sequence-step guard, so the same
    template can be sent to a lead at any time (even if that step was already
    sent). Pass ``manual`` explicitly to override the default.
    """
    lead = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not lead:
        raise DraftError("lead not found", status=404)

    if template_id is not None:
        tpl = conn.execute("SELECT * FROM templates WHERE id=?", (template_id,)).fetchone()
        if not tpl:
            raise DraftError("template not found", status=404)
        step = tpl["step"] if tpl["step"] is not None else (lead["step"] or 0)
    else:
        step = lead["step"] or 0
        if step >= 5:
            raise DraftError("sequence complete for this lead", status=400)
        tpl = next_template(conn, step)
        if not tpl:
            raise DraftError(f"no template for step {step}", status=400)

    founders = [dict(f) for f in conn.execute(
        "SELECT * FROM founders WHERE lead_id=? ORDER BY id", (lead_id,)).fetchall()]
    reachable = render.reachable(founders)
    if not reachable:
        raise DraftError("no reachable founder email (all missing or marked invalid)", status=400)
    vars_ = render.variables(dict(lead), founders)
    subject = render.render(tpl["subject_tpl"], vars_)
    if step > 0:
        orig = original_subject(conn, lead_id)
        if orig and not subject:
            subject = orig if orig.lower().startswith("re:") else f"Re: {orig}"
    body = render.render(tpl["body_tpl"], vars_)
    to_addrs = ", ".join(f["email"] for f in reachable)
    if manual is None:
        manual = template_id is not None

    # Replace the existing draft and insert a fresh one in a single transaction.
    with conn:
        conn.execute("DELETE FROM messages WHERE lead_id=? AND status='draft'", (lead_id,))
        cur = conn.execute(
            """INSERT INTO messages (lead_id, step, to_addrs, subject, body, status, attach_resume, manual)
               VALUES (?,?,?,?,?, 'draft', 1, ?)""",
            (lead_id, step, to_addrs, subject, body, 1 if manual else 0),
        )
    return conn.execute("SELECT * FROM messages WHERE id=?", (cur.lastrowid,)).fetchone()

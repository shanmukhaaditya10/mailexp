"""Outreach: drafts, queue, sending, outbox reconciliation, and templates.

All sending is funneled through the in-process sender worker; these endpoints
only build drafts and enqueue jobs. Safety limits (daily cap, send window) are
enforced inside the send service, not here.
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .. import sender
from ..db import get_conn
from ..services import drafts as drafts_svc
from ..services import leads as leads_svc
from ..services import sending as sending_svc
from ..services import settings as settings_svc
from . import service_error

router = APIRouter(prefix="/api", tags=["outreach"])


class MessageUpdate(BaseModel):
    subject: str | None = None
    body: str | None = None
    attach_resume: int | None = None


class ResolvePayload(BaseModel):
    sent: bool


class RetryPayload(BaseModel):
    confirm: bool = False


class TemplateUpdate(BaseModel):
    subject_tpl: str | None = None
    body_tpl: str | None = None


class Selection(BaseModel):
    """Optional manual selection for queue operations.

    When `lead_ids` is omitted the operation applies to every lead due for their
    next step. When `template_id` is given, that exact template is used instead
    of the lead's automatic next sequence step.
    """
    lead_ids: list[int] | None = None
    template_id: int | None = None


def _dry_run():
    return settings_svc.get_setting("dry_run", "1") == "1"


def _selected_leads(conn, list_id, lead_ids):
    if lead_ids:
        rows = []
        for lid in lead_ids:
            row = conn.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone()
            if row:
                rows.append(row)
        return rows
    return leads_svc.due_leads(conn, list_id)


# ---------- queue & drafts ----------
@router.get("/queue")
def get_queue(list_id: str | None = Query(None, alias="list")):
    conn = get_conn()
    try:
        return [leads_svc.lead_dict(conn, r) for r in leads_svc.due_leads(conn, list_id)]
    finally:
        conn.close()


@router.post("/leads/{lead_id}/draft")
def create_draft(lead_id: int):
    conn = get_conn()
    try:
        try:
            return dict(drafts_svc.build_draft(conn, lead_id))
        except drafts_svc.DraftError as e:
            raise service_error(e)
    finally:
        conn.close()


@router.post("/queue/drafts")
def create_all_drafts(list_id: str | None = Query(None, alias="list"), payload: Selection | None = None):
    lead_ids = payload.lead_ids if payload else None
    template_id = payload.template_id if payload else None
    conn = get_conn()
    try:
        created, errors = [], []
        for lead in _selected_leads(conn, list_id, lead_ids):
            try:
                created.append(drafts_svc.build_draft(conn, lead["id"], template_id=template_id)["id"])
            except drafts_svc.DraftError as e:
                errors.append({"lead_id": lead["id"], "error": str(e)})
        return {"drafts_created": len(created), "ids": created, "errors": errors[:20]}
    finally:
        conn.close()


# ---------- messages ----------
@router.get("/messages/{message_id}")
def get_message(message_id: int):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        if not row:
            raise HTTPException(404, "message not found")
        return dict(row)
    finally:
        conn.close()


@router.put("/messages/{message_id}")
def update_message(message_id: int, payload: MessageUpdate):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        if not row:
            raise HTTPException(404, "message not found")
        if row["status"] in ("sent", "sending"):
            raise HTTPException(409, f"message is already {row['status']}")
        fields, vals = [], []
        for k in ("subject", "body", "attach_resume"):
            v = getattr(payload, k)
            if v is not None:
                fields.append(f"{k}=?")
                vals.append(v)
        if fields:
            vals.append(message_id)
            with conn:
                conn.execute(f"UPDATE messages SET {', '.join(fields)} WHERE id=?", vals)
        return dict(conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone())
    finally:
        conn.close()


# ---------- sending ----------
@router.post("/messages/{message_id}/send")
def send_one(message_id: int):
    return sender.enqueue([message_id], dry_run=_dry_run())


@router.post("/queue/send")
def send_queue(list_id: str | None = Query(None, alias="list"), payload: Selection | None = None):
    lead_ids = payload.lead_ids if payload else None
    template_id = payload.template_id if payload else None
    conn = get_conn()
    try:
        dry = _dry_run()
        cap = settings_svc.get_setting_int("daily_cap", 40)
        sent_today = sending_svc.count_sent_today(conn)
        reserved = sending_svc.reserved_today(conn)
        limit = None if (dry or lead_ids) else max(cap - reserved, 0)
        ids = []
        for lead in _selected_leads(conn, list_id, lead_ids):
            if limit is not None and len(ids) >= limit:
                break
            if template_id is not None:
                try:
                    draft = drafts_svc.build_draft(conn, lead["id"], template_id=template_id)
                except drafts_svc.DraftError:
                    continue
            else:
                draft = conn.execute(
                    "SELECT * FROM messages WHERE lead_id=? AND status='draft' ORDER BY id LIMIT 1",
                    (lead["id"],),
                ).fetchone()
                if not draft:
                    try:
                        draft = drafts_svc.build_draft(conn, lead["id"])
                    except drafts_svc.DraftError:
                        continue
            ids.append(draft["id"])
    finally:
        conn.close()

    if not ids:
        note = None
        if limit == 0:
            note = f"daily cap reached ({reserved}/{cap})"
        return {"dry_run": dry, "cap": cap, "sent_today": sent_today, "reserved": reserved,
                "queued": 0, "job_id": None, "status": "done", "note": note}
    job = sender.enqueue(ids, dry_run=dry)
    return {"dry_run": dry, "cap": cap, "sent_today": sent_today, "reserved": reserved,
            "queued": len(ids), "job_id": job["id"], "status": job["status"]}


# ---------- outbox (manual review) ----------
@router.get("/outbox")
def get_outbox():
    conn = get_conn()
    try:
        return sending_svc.list_review(conn)
    finally:
        conn.close()


@router.post("/messages/{message_id}/resolve")
def resolve_message(message_id: int, payload: ResolvePayload):
    conn = get_conn()
    try:
        try:
            return sending_svc.resolve_message(conn, message_id, payload.sent)
        except sending_svc.SendError as e:
            raise service_error(e)
    finally:
        conn.close()


@router.post("/messages/{message_id}/reconcile")
def reconcile_message(message_id: int):
    conn = get_conn()
    try:
        try:
            return sending_svc.reconcile_unknown(conn, message_id)
        except sending_svc.SendError as e:
            raise service_error(e)
    finally:
        conn.close()


@router.post("/messages/{message_id}/retry")
def retry_message(message_id: int, payload: RetryPayload | None = None):
    confirm = bool(payload and payload.confirm)
    conn = get_conn()
    try:
        row = conn.execute("SELECT status FROM messages WHERE id=?", (message_id,)).fetchone()
        if not row:
            raise HTTPException(404, "message not found")
        status = row["status"]
        if status == "send_unknown":
            # Always check Gmail first; resend only when it is confidently absent
            # AND the caller explicitly confirmed it was not delivered.
            try:
                check = sending_svc.reconcile_unknown(conn, message_id)
            except sending_svc.SendError as e:
                raise service_error(e)
            if check["found"]:
                return {"reconciled": True, "resent": False, "message": check["message"]}
            if not confirm:
                raise HTTPException(
                    409,
                    detail={
                        "requires_confirmation": True,
                        "message": "Delivery could not be confirmed in Gmail Sent Mail. "
                                   "Resend only if you are certain it was not delivered.",
                    },
                )
            with conn:
                conn.execute("UPDATE messages SET status='send_failed' WHERE id=?", (message_id,))
        elif status not in ("send_failed", "draft", "dry_run"):
            raise HTTPException(409, f"cannot retry a '{status}' message")
    finally:
        conn.close()
    return {"resent": True, **sender.enqueue([message_id], dry_run=_dry_run())}


# ---------- send jobs ----------
@router.get("/send-jobs")
def get_send_jobs():
    return sender.list_jobs()


@router.post("/send-jobs/{job_id}/cancel")
def cancel_send_job(job_id: int):
    return sender.cancel(job_id)


# ---------- templates ----------
@router.get("/templates")
def list_templates():
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM templates ORDER BY step").fetchall()]
    finally:
        conn.close()


@router.put("/templates/{template_id}")
def update_template(template_id: int, payload: TemplateUpdate):
    conn = get_conn()
    try:
        fields, vals = [], []
        if payload.subject_tpl is not None:
            fields.append("subject_tpl=?")
            vals.append(payload.subject_tpl)
        if payload.body_tpl is not None:
            fields.append("body_tpl=?")
            vals.append(payload.body_tpl)
        if fields:
            vals.append(template_id)
            with conn:
                conn.execute(f"UPDATE templates SET {', '.join(fields)} WHERE id=?", vals)
        row = conn.execute("SELECT * FROM templates WHERE id=?", (template_id,)).fetchone()
        if not row:
            raise HTTPException(404, "template not found")
        return dict(row)
    finally:
        conn.close()

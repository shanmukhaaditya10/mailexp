"""Lead listing, editing, list discovery, import, and overview stats."""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .. import import_leads
from ..db import get_conn
from ..services import leads as leads_svc

router = APIRouter(prefix="/api", tags=["leads"])


class LeadUpdate(BaseModel):
    context: str | None = None
    status: str | None = None


@router.get("/leads")
def list_leads(list_id: str | None = Query(None, alias="list")):
    conn = get_conn()
    try:
        if list_id:
            rows = conn.execute(
                "SELECT * FROM leads WHERE source=? ORDER BY company", (list_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM leads ORDER BY company").fetchall()
        return [leads_svc.lead_dict(conn, r) for r in rows]
    finally:
        conn.close()


@router.get("/leads/{lead_id}")
def get_lead(lead_id: int):
    conn = get_conn()
    try:
        lead = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not lead:
            raise HTTPException(404, "lead not found")
        d = leads_svc.lead_dict(conn, lead)
        msgs = conn.execute(
            "SELECT * FROM messages WHERE lead_id=? ORDER BY id", (lead_id,)
        ).fetchall()
        reps = conn.execute(
            "SELECT * FROM replies WHERE lead_id=? ORDER BY id", (lead_id,)
        ).fetchall()
        d["messages"] = [dict(m) for m in msgs]
        d["replies"] = [dict(r) for r in reps]
        return d
    finally:
        conn.close()


@router.put("/leads/{lead_id}")
def update_lead(lead_id: int, payload: LeadUpdate):
    conn = get_conn()
    try:
        if payload.context is not None:
            conn.execute("UPDATE leads SET context=? WHERE id=?", (payload.context, lead_id))
        if payload.status is not None:
            conn.execute("UPDATE leads SET status=? WHERE id=?", (payload.status, lead_id))
        conn.commit()
        lead = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not lead:
            raise HTTPException(404, "lead not found")
        return leads_svc.lead_dict(conn, lead)
    finally:
        conn.close()


@router.get("/lists")
def get_lists():
    conn = get_conn()
    try:
        counts = {
            r["source"]: r["n"]
            for r in conn.execute(
                "SELECT source, COUNT(*) AS n FROM leads GROUP BY source"
            ).fetchall()
        }
        items = []
        for p in import_leads.list_files():
            lid = import_leads.list_id(p)
            rel = p.relative_to(import_leads.XLS_DIR).as_posix()
            folder = rel.rsplit("/", 1)[0] if "/" in rel else None
            items.append({
                "id": lid,
                "name": p.stem,
                "folder": folder,
                "file": rel,
                "count": counts.get(lid, 0),
            })
        return items
    finally:
        conn.close()


@router.post("/import")
def do_import(list_id: str | None = Query(None, alias="list")):
    if list_id:
        path = import_leads.resolve_list(list_id)
        if not path:
            raise HTTPException(404, f"no xlsx found for list '{list_id}'")
        return import_leads.import_from_xlsx(path, source=list_id)
    return import_leads.import_from_xlsx()


@router.get("/stats")
def stats(list_id: str | None = Query(None, alias="list")):
    conn = get_conn()
    try:
        args = (list_id,) if list_id else ()
        where = " WHERE source=?" if list_id else ""
        leads = conn.execute("SELECT COUNT(*) FROM leads" + where, args).fetchone()[0]
        founders = conn.execute(
            "SELECT COUNT(*) FROM founders f JOIN leads l ON l.id=f.lead_id"
            + (" WHERE l.source=?" if list_id else ""),
            args,
        ).fetchone()[0]
        sent = conn.execute(
            "SELECT COUNT(*) FROM messages m JOIN leads l ON l.id=m.lead_id WHERE m.status='sent'"
            + (" AND l.source=?" if list_id else ""),
            args,
        ).fetchone()[0]
        replied = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status='replied'"
            + (" AND source=?" if list_id else ""),
            args,
        ).fetchone()[0]
        return {"leads": leads, "founders": founders, "sent": sent, "replied": replied}
    finally:
        conn.close()

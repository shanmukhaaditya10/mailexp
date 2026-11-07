"""Mailbox sync (IMAP) and reply listing."""
from fastapi import APIRouter, HTTPException, Query

from ..db import get_conn
from ..services import mailbox_sync

router = APIRouter(prefix="/api", tags=["mailbox"])


def _sync(conn, which, limit):
    try:
        if which == "sent":
            return mailbox_sync.sync_sent(conn, limit)
        if which == "replies":
            return mailbox_sync.sync_replies(conn, limit)
        return mailbox_sync.sync_all(conn, limit)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"sync failed: {e}")


@router.post("/sync")
def sync_all(limit: int = 50):
    conn = get_conn()
    try:
        return _sync(conn, "all", limit)
    finally:
        conn.close()


@router.post("/sync/sent")
def sync_sent(limit: int = 50):
    conn = get_conn()
    try:
        return _sync(conn, "sent", limit)
    finally:
        conn.close()


@router.post("/sync/replies")
def sync_replies(limit: int = 50):
    conn = get_conn()
    try:
        return _sync(conn, "replies", limit)
    finally:
        conn.close()


@router.get("/replies")
def list_replies(list_id: str | None = Query(None, alias="list")):
    conn = get_conn()
    try:
        if list_id:
            rows = conn.execute(
                """SELECT r.* FROM replies r JOIN leads l ON l.id = r.lead_id
                   WHERE l.source=? ORDER BY r.received_at DESC LIMIT 200""",
                (list_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM replies ORDER BY received_at DESC LIMIT 200"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

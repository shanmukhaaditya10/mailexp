"""Lead serialization and queue-eligibility queries."""
from ..util import now_iso


def lead_dict(conn, lead):
    founders = conn.execute(
        "SELECT * FROM founders WHERE lead_id=? ORDER BY id", (lead["id"],)
    ).fetchall()
    d = dict(lead)
    d["founders"] = [dict(f) for f in founders]
    return d


def due_leads(conn, list_id=None):
    """Leads eligible for their next send: not finished, step < 5, and due."""
    sql = """SELECT * FROM leads
           WHERE status NOT IN ('replied','bounced','done')
             AND step < 5
             AND (next_due_at IS NULL OR next_due_at <= ?)"""
    params = [now_iso()]
    if list_id:
        sql += " AND source = ?"
        params.append(list_id)
    sql += " ORDER BY company"
    return conn.execute(sql, params).fetchall()

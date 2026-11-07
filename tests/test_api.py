from tests.conftest import add_founder, add_lead


def test_stats_defaults(client):
    r = client.get("/api/stats")
    assert r.status_code == 200
    assert r.json() == {"leads": 0, "founders": 0, "sent": 0, "replied": 0}


def test_settings_roundtrip(client):
    r = client.put("/api/settings", json={"daily_cap": "7"})
    assert r.status_code == 200
    assert r.json()["daily_cap"] == "7"
    assert client.get("/api/settings").json()["daily_cap"] == "7"


def test_templates_have_defaults(client):
    r = client.get("/api/templates")
    assert r.status_code == 200
    assert len(r.json()) >= 5


def test_intro_template_default(client):
    t0 = next(t for t in client.get("/api/templates").json() if t["step"] == 0)
    assert t0["subject_tpl"] == "An intro and an ask | Aditya"
    assert "Hey {{names}}," in t0["body_tpl"]
    assert "GoQuant" in t0["body_tpl"]


def test_import_unknown_list_is_404(client):
    r = client.post("/api/import?list=does-not-exist")
    assert r.status_code == 404


def test_outbox_empty(client):
    r = client.get("/api/outbox")
    assert r.status_code == 200
    assert r.json() == []


def test_draft_then_fetch_message(client, temp_db):
    conn = temp_db.get_conn()
    try:
        lead = add_lead(conn, company="Acme")
        add_founder(conn, lead, email="jane@acme.com")
    finally:
        conn.close()

    r = client.post(f"/api/leads/{lead}/draft")
    assert r.status_code == 200
    draft = r.json()
    assert draft["status"] == "draft"
    assert draft["to_addrs"] == "jane@acme.com"

    r = client.get(f"/api/messages/{draft['id']}")
    assert r.status_code == 200


def test_send_dry_run_enqueues_job(client, temp_db):
    conn = temp_db.get_conn()
    try:
        lead = add_lead(conn, company="Acme")
        add_founder(conn, lead, email="jane@acme.com")
    finally:
        conn.close()
    draft = client.post(f"/api/leads/{lead}/draft").json()

    r = client.post(f"/api/messages/{draft['id']}/send")
    assert r.status_code == 200
    assert r.json()["dry_run"] is True


def test_retry_rejects_send_unknown(client, temp_db):
    conn = temp_db.get_conn()
    try:
        lead = add_lead(conn, company="Acme")
        cur = conn.execute(
            "INSERT INTO messages (lead_id, to_addrs, subject, body, status) "
            "VALUES (?,?,?,?,'send_unknown')",
            (lead, "jane@acme.com", "Hi", "x"),
        )
        conn.commit()
        mid = cur.lastrowid
    finally:
        conn.close()
    assert client.post(f"/api/messages/{mid}/retry").status_code == 409


def test_resolve_missing_message_404(client):
    r = client.post("/api/messages/9999/resolve", json={"sent": True})
    assert r.status_code == 404


def test_draft_selected_uses_chosen_template(client, temp_db):
    conn = temp_db.get_conn()
    try:
        l1 = add_lead(conn, company="Acme")
        add_founder(conn, l1, email="jane@acme.com")
        l2 = add_lead(conn, company="Beta")
        add_founder(conn, l2, email="bob@beta.com")
    finally:
        conn.close()
    t1 = next(t for t in client.get("/api/templates").json() if t["step"] == 1)

    r = client.post("/api/queue/drafts", json={"lead_ids": [l1], "template_id": t1["id"]})
    assert r.status_code == 200
    assert r.json()["drafts_created"] == 1

    conn = temp_db.get_conn()
    try:
        m1 = conn.execute("SELECT * FROM messages WHERE lead_id=?", (l1,)).fetchone()
        m2 = conn.execute("SELECT * FROM messages WHERE lead_id=?", (l2,)).fetchone()
    finally:
        conn.close()
    assert m1 is not None and m1["step"] == 1
    assert m2 is None


def test_send_selected_only_queues_chosen(client, temp_db):
    conn = temp_db.get_conn()
    try:
        l1 = add_lead(conn, company="Acme")
        add_founder(conn, l1, email="jane@acme.com")
        l2 = add_lead(conn, company="Beta")
        add_founder(conn, l2, email="bob@beta.com")
    finally:
        conn.close()
    t0 = next(t for t in client.get("/api/templates").json() if t["step"] == 0)

    r = client.post("/api/queue/send", json={"lead_ids": [l1], "template_id": t0["id"]})
    assert r.status_code == 200
    assert r.json()["queued"] == 1

    conn = temp_db.get_conn()
    try:
        n2 = conn.execute("SELECT COUNT(*) c FROM messages WHERE lead_id=?", (l2,)).fetchone()["c"]
    finally:
        conn.close()
    assert n2 == 0


def test_send_selected_template_to_contacted_lead(client, temp_db):
    # A lead that is already contacted (and not due) can still be sent a chosen
    # template; the built message is marked manual so re-sends are allowed.
    conn = temp_db.get_conn()
    try:
        lead = add_lead(conn, company="Acme", status="contacted", step=1)
        add_founder(conn, lead, email="jane@acme.com")
    finally:
        conn.close()
    t0 = next(t for t in client.get("/api/templates").json() if t["step"] == 0)

    r = client.post("/api/queue/send", json={"lead_ids": [lead], "template_id": t0["id"]})
    assert r.status_code == 200
    assert r.json()["queued"] == 1

    conn = temp_db.get_conn()
    try:
        m = conn.execute(
            "SELECT step, manual FROM messages WHERE lead_id=?", (lead,)
        ).fetchone()
    finally:
        conn.close()
    assert m is not None
    assert m["step"] == 0 and m["manual"] == 1

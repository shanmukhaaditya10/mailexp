import pytest
from openpyxl import Workbook

from app import import_leads

HEADER = ["Company", "Founder Name", "Email (guessed)", "Domain", "Team Size"]


def make_xlsx(path, rows, header=HEADER):
    wb = Workbook()
    ws = wb.active
    ws.title = "Companies"
    ws.append(header)
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


def test_import_basic(tmp_path, temp_db):
    p = make_xlsx(tmp_path / "list.xlsx", [
        ["Acme", "Jane Doe", "jane@acme.com", "", 10],
        ["Acme", "John Roe", "john@acme.com", "", 10],
    ])
    out = import_leads.import_from_xlsx(p, source="list")
    assert out["leads"] == 1
    assert out["founders"] == 2
    conn = temp_db.get_conn()
    try:
        lead = conn.execute("SELECT * FROM leads").fetchone()
        assert lead["source"] == "list"
        assert lead["domain"] == "acme.com"  # derived from the founder email
        assert conn.execute("SELECT COUNT(*) FROM founders").fetchone()[0] == 2
    finally:
        conn.close()


def test_import_detects_duplicate_rows(tmp_path, temp_db):
    row = ["Acme", "Jane Doe", "jane@acme.com", "", 10]
    p = make_xlsx(tmp_path / "list.xlsx", [row, list(row)])
    out = import_leads.import_from_xlsx(p, source="list")
    assert out["duplicates"] == 1
    assert out["founders"] == 1


def test_import_flags_invalid_email_without_storing(tmp_path, temp_db):
    p = make_xlsx(tmp_path / "list.xlsx", [["Acme", "Jane Doe", "not-an-email", "", 10]])
    out = import_leads.import_from_xlsx(p, source="list")
    assert out["error_count"] == 1
    conn = temp_db.get_conn()
    try:
        founder = conn.execute("SELECT * FROM founders").fetchone()
        assert founder["email"] is None
    finally:
        conn.close()


def test_import_preserves_existing_on_blank(tmp_path, temp_db):
    p = make_xlsx(tmp_path / "list.xlsx", [["Acme", "Jane Doe", "jane@acme.com", "", 10]])
    import_leads.import_from_xlsx(p, source="list")
    # Re-import same person but with a blank email; the stored email must survive.
    blank = make_xlsx(tmp_path / "list.xlsx", [["Acme", "Jane Doe", None, "", 10]])
    out = import_leads.import_from_xlsx(blank, source="list")
    assert out["founders_updated"] == 0
    conn = temp_db.get_conn()
    try:
        founder = conn.execute("SELECT * FROM founders").fetchone()
        assert founder["email"] == "jane@acme.com"
    finally:
        conn.close()


def test_import_missing_required_column(tmp_path, temp_db):
    p = make_xlsx(tmp_path / "list.xlsx", [["Jane", "jane@acme.com"]], header=["Founder Name", "Email (guessed)"])
    with pytest.raises(ValueError):
        import_leads.import_from_xlsx(p, source="list")


def test_resolve_list_rejects_traversal(tmp_path, temp_db, monkeypatch):
    monkeypatch.setattr(import_leads, "ROOT", tmp_path)
    assert import_leads.resolve_list("../secrets") is None
    assert import_leads.resolve_list("nope") is None

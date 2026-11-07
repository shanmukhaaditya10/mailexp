"""Shared test fixtures.

Every test runs against a throwaway SQLite file and no Gmail credentials, so
nothing ever touches the real database, SMTP, or IMAP.
"""
import pytest


@pytest.fixture(autouse=True)
def _no_credentials(monkeypatch):
    # Guarantee no test can accidentally reach Gmail, even if a .env exists.
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    from app import db

    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    return db


@pytest.fixture()
def conn(temp_db):
    c = temp_db.get_conn()
    yield c
    c.close()


@pytest.fixture()
def client(temp_db):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings (key,value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def add_lead(conn, company="Acme", domain=None, source="test", status="not_contacted", step=0):
    cur = conn.execute(
        "INSERT INTO leads (company, domain, source, status, step) VALUES (?,?,?,?,?)",
        (company, domain, source, status, step),
    )
    conn.commit()
    return cur.lastrowid


def add_founder(conn, lead_id, name="Jane", email="jane@acme.com"):
    cur = conn.execute(
        "INSERT INTO founders (lead_id, name, first_name, email) VALUES (?,?,?,?)",
        (lead_id, name, name.split()[0], email),
    )
    conn.commit()
    return cur.lastrowid


def add_message(conn, lead_id, status="draft", to="jane@acme.com", step=0):
    cur = conn.execute(
        "INSERT INTO messages (lead_id, step, to_addrs, subject, body, status) VALUES (?,?,?,?,?,?)",
        (lead_id, step, to, "Hi", "Body", status),
    )
    conn.commit()
    return cur.lastrowid

"""New defaults (40/2) are seeded, and legacy default values are migrated.

The migration only advances a row that still holds a superseded *default*; a
value the user deliberately customised is preserved.
"""
from app import db
from app.db import get_conn


def _value(conn, key):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def test_new_database_gets_new_defaults(temp_db):
    c = get_conn()
    try:
        assert _value(c, "daily_cap") == "40"
        assert _value(c, "min_send_interval_seconds") == "2"
    finally:
        c.close()


def test_old_defaults_are_migrated(temp_db):
    c = get_conn()
    try:
        c.execute("UPDATE settings SET value='25' WHERE key='daily_cap'")
        c.execute("UPDATE settings SET value='1' WHERE key='min_send_interval_seconds'")
        c.commit()
    finally:
        c.close()

    db.init_db()  # re-run startup against the existing file

    c = get_conn()
    try:
        assert _value(c, "daily_cap") == "40"
        assert _value(c, "min_send_interval_seconds") == "2"
    finally:
        c.close()


def test_legacy_interval_default_is_migrated(temp_db):
    c = get_conn()
    try:
        c.execute("UPDATE settings SET value='20' WHERE key='min_send_interval_seconds'")
        c.commit()
    finally:
        c.close()

    db.init_db()

    c = get_conn()
    try:
        assert _value(c, "min_send_interval_seconds") == "2"
    finally:
        c.close()


def test_customised_values_are_preserved(temp_db):
    c = get_conn()
    try:
        c.execute("UPDATE settings SET value='30' WHERE key='daily_cap'")
        c.execute("UPDATE settings SET value='0.5' WHERE key='min_send_interval_seconds'")
        c.commit()
    finally:
        c.close()

    db.init_db()

    c = get_conn()
    try:
        assert _value(c, "daily_cap") == "30"
        assert _value(c, "min_send_interval_seconds") == "0.5"
    finally:
        c.close()

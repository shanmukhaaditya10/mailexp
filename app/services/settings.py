"""Settings accessors backed by the `settings` key/value table."""
from ..db import get_conn


def get_setting(key, default=None, conn=None):
    own = conn is None
    conn = conn or get_conn()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        if own:
            conn.close()


def get_setting_int(key, default):
    try:
        return int(get_setting(key, default))
    except (TypeError, ValueError):
        return default


def get_setting_float(key, default):
    try:
        return float(get_setting(key, default))
    except (TypeError, ValueError):
        return default


def set_settings(values, conn=None):
    own = conn is None
    conn = conn or get_conn()
    try:
        for k, v in values.items():
            conn.execute(
                "INSERT INTO settings (key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, v),
            )
        conn.commit()
    finally:
        if own:
            conn.close()


def all_settings():
    conn = get_conn()
    try:
        return {r["key"]: r["value"] for r in conn.execute("SELECT * FROM settings").fetchall()}
    finally:
        conn.close()

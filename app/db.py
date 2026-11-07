import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB_PATH = DATA / "mailexp.db"

# The xlsx used when a list/source is not specified (legacy default).
DEFAULT_LIST_STEM = "yc_founders_outreach"

# SQLite tuning. WAL lets reads proceed while the sender worker writes; a busy
# timeout avoids spurious "database is locked" errors under brief contention.
BUSY_TIMEOUT_MS = 5000

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    batch TEXT,
    team_size INTEGER,
    location TEXT,
    domain TEXT,
    website TEXT,
    source TEXT,
    context TEXT,
    status TEXT DEFAULT 'not_contacted',
    step INTEGER DEFAULT 0,
    last_sent_at TEXT,
    next_due_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS founders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL,
    name TEXT,
    first_name TEXT,
    title TEXT,
    email TEXT,
    linkedin TEXT,
    twitter TEXT,
    invalid INTEGER DEFAULT 0,
    FOREIGN KEY (lead_id) REFERENCES leads(id)
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL,
    step INTEGER,
    to_addrs TEXT,
    subject TEXT,
    body TEXT,
    status TEXT DEFAULT 'draft',
    message_id TEXT,
    thread_id TEXT,
    sent_at TEXT,
    attach_resume INTEGER DEFAULT 1,
    send_attempts INTEGER DEFAULT 0,
    last_error TEXT,
    last_attempt_at TEXT,
    client_message_id TEXT,
    manual INTEGER DEFAULT 0,
    FOREIGN KEY (lead_id) REFERENCES leads(id)
);
CREATE TABLE IF NOT EXISTS replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER,
    thread_id TEXT,
    from_addr TEXT,
    message_id TEXT,
    snippet TEXT,
    received_at TEXT,
    kind TEXT,
    company TEXT,
    match_note TEXT
);
CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    step INTEGER,
    subject_tpl TEXT,
    body_tpl TEXT,
    is_default INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

# Additive migrations for databases created by older versions. Each entry is
# (table, column, DDL). Applied only when the column is missing.
MIGRATIONS = [
    ("replies", "company", "ALTER TABLE replies ADD COLUMN company TEXT"),
    ("replies", "message_id", "ALTER TABLE replies ADD COLUMN message_id TEXT"),
    ("replies", "match_note", "ALTER TABLE replies ADD COLUMN match_note TEXT"),
    ("messages", "send_attempts", "ALTER TABLE messages ADD COLUMN send_attempts INTEGER DEFAULT 0"),
    ("messages", "last_error", "ALTER TABLE messages ADD COLUMN last_error TEXT"),
    ("messages", "last_attempt_at", "ALTER TABLE messages ADD COLUMN last_attempt_at TEXT"),
    ("messages", "client_message_id", "ALTER TABLE messages ADD COLUMN client_message_id TEXT"),
    # 1 = the user explicitly chose a template for this message; such messages are
    # exempt from the one-per-sequence-step guard so they can be sent anytime.
    ("messages", "manual", "ALTER TABLE messages ADD COLUMN manual INTEGER DEFAULT 0"),
    # 1 = this founder's address is known bad (bounced / flagged Invalid in the
    # workbook). Excluded from recipients and from the greeting.
    ("founders", "invalid", "ALTER TABLE founders ADD COLUMN invalid INTEGER DEFAULT 0"),
]

# Settings whose superseded *default* value should be advanced to the new
# default on startup. A row is only touched when it still holds a previous
# default, so a value the user deliberately customised is preserved. Applied
# idempotently on every startup.
SETTINGS_MIGRATIONS = {
    "daily_cap": {"25": "40"},
    "min_send_interval_seconds": {"20": "2", "1": "2"},
}

# Indexes used by real queries: queue eligibility, list filtering, message
# status/date, and mailbox matching. All created idempotently.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_leads_source ON leads(source);
CREATE INDEX IF NOT EXISTS idx_leads_due ON leads(status, step, next_due_at);
CREATE INDEX IF NOT EXISTS idx_leads_domain ON leads(domain);
CREATE INDEX IF NOT EXISTS idx_founders_lead ON founders(lead_id);
CREATE INDEX IF NOT EXISTS idx_founders_email ON founders(email);
CREATE INDEX IF NOT EXISTS idx_messages_lead ON messages(lead_id);
CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);
CREATE INDEX IF NOT EXISTS idx_messages_sent_at ON messages(status, sent_at);
CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_message_id
    ON messages(message_id) WHERE message_id IS NOT NULL AND message_id <> '';
-- A lead's automatic sequence step is only ever delivered once, no matter how
-- many message rows or retries existed. This is the hard backstop behind the
-- application-level checks. Manually-sent messages (manual=1) are exempt so a
-- chosen template can be sent to a lead at any time.
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_sent_step
    ON messages(lead_id, step) WHERE status='sent' AND manual=0;
CREATE INDEX IF NOT EXISTS idx_replies_lead ON replies(lead_id);
CREATE INDEX IF NOT EXISTS idx_replies_received ON replies(received_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_replies_message_id
    ON replies(message_id) WHERE message_id IS NOT NULL AND message_id <> '';
"""

DEFAULT_TEMPLATES = [
    (
        "Intro",
        0,
        "An intro and an ask | Aditya",
        """**TL;DR:** Software engineer + tech-enabled generalist, who genuinely wants to work at {{company}}. Prev worked at GoQuant. I can build, figure things out, and help with whatever needs doing. I'd love a chance to prove myself.

Hey {{names}},

I'm Aditya, based in India, and I'm really keen to work in a startup environment like yours. I want to build alongside a small team.

About me:

- Worked as a Backend Engineer at [GoQuant](https://goquant.io), building trading infrastructure handling $100M+ in daily trading volume. My role was affected by downsizing :( Since then, I've continued building full-stack products and AI applications.
- **I build things to solve my own problems.** I even built a tool to manage my outreach and remind me to follow up (in case you miss this email or aren't in the mood to respond atm). I like finding repetitive problems and building tools to make them easier.
- **SWE but can do more than code.** I enjoy taking ownership, figuring things out independently, and moving fast. Whether it's building a feature or automating a workflow, or even talking to customer/support.

I'd genuinely love a chance to work with your team. I'd absolutely love if you can give me a take-home or a feature to build, or let me work alongside someone on your product. I want to demonstrate what I can bring.

I'd appreciate it A LOT if you consider giving me a shot. Looking forward to hearing from you!

Best,
Aditya

**P.S.** If this gets buried, you'll probably hear from me again. I'm serious about this opportunity :)""",
    ),
    (
        "Follow-up 1",
        1,
        "",
        """Hey {{first_name}},

Bumping this in case my last email got buried! I'm still genuinely keen on {{company}} and would love a chance to show you what I can bring to the team.

Happy to build something small or jump on a quick call if that helps :)

Best,  
Aditya""",
    ),
    (
        "Follow-up 2",
        2,
        "",
        """Hey {{first_name}},

Quick nudge in case this got buried. I'd genuinely love a shot at {{company}}.

Give me any small task and I'll ship it. Happy to work free for a month to prove it.

Worth 10 minutes?""",
    ),
    (
        "Follow-up 3",
        3,
        "",
        """Hey {{first_name}},

Me again. I know inboxes are brutal, so just flagging this one last time.

If you ever want someone who can build fast and figure things out, I'm around and happy to prove it.

Either way, rooting for {{company}}.""",
    ),
    (
        "Follow-up 4 (last)",
        4,
        "",
        """Hey {{first_name}},

Last note from me, promise. If the timing's wrong I completely get it.

If there's ever a fit, I'm one reply away.

All the best with {{company}}!""",
    ),
]


def get_conn():
    """Open a fresh connection for a single operation/transaction.

    A connection is never shared between threads: the FastAPI request threads
    and the sender worker each open their own. WAL + busy_timeout make short
    concurrent writes safe.
    """
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _table_columns(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def migrate(conn):
    """Apply additive schema migrations. Idempotent."""
    # legacy: leads.source + backfill
    if "source" not in _table_columns(conn, "leads"):
        conn.execute("ALTER TABLE leads ADD COLUMN source TEXT")
        conn.execute("UPDATE leads SET source=? WHERE source IS NULL", (DEFAULT_LIST_STEM,))
    for table, column, ddl in MIGRATIONS:
        if column not in _table_columns(conn, table):
            conn.execute(ddl)


def _migrate_settings(conn):
    """Advance settings still holding a superseded default value."""
    for key, mapping in SETTINGS_MIGRATIONS.items():
        for old, new in mapping.items():
            conn.execute(
                "UPDATE settings SET value=? WHERE key=? AND value=?", (new, key, old)
            )


def _rebuild_step_index(conn):
    """Drop the pre-`manual` step index so `_apply_indexes` recreates it.

    Older databases have `idx_messages_sent_step` without the `manual=0`
    predicate. A UNIQUE index cannot be altered in place, so it is dropped here
    and recreated (with the new predicate) by `_apply_indexes`. Idempotent: once
    rebuilt, its SQL contains `manual` and it is left alone.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_messages_sent_step'"
    ).fetchone()
    if row and row["sql"] and "manual" not in row["sql"]:
        conn.execute("DROP INDEX idx_messages_sent_step")


def init_db():
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        migrate(conn)
        _rebuild_step_index(conn)
        _apply_indexes(conn)
        n = conn.execute("SELECT COUNT(*) FROM templates").fetchone()[0]
        if n == 0:
            conn.executemany(
                "INSERT INTO templates (name, step, subject_tpl, body_tpl, is_default) VALUES (?,?,?,?,1)",
                DEFAULT_TEMPLATES,
            )
        defaults = {
            "daily_cap": "40",
            "dry_run": "1",
            "resume_path": str(DATA / "resume.pdf"),
            "send_window": "09:00-18:00",
            "step_gap_days": "2",
            "min_send_interval_seconds": "2",
            "signature": "",
        }
        for k, v in defaults.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", (k, v))
        _migrate_settings(conn)
        conn.commit()
    finally:
        conn.close()


def _apply_indexes(conn):
    """Create indexes one by one.

    A UNIQUE index can fail on a database that already violates it (e.g. an
    older file with duplicate sent records). We never drop data, so a failing
    index is reported and skipped rather than crashing startup; the
    application-level guards still apply.
    """
    for stmt in (s.strip() for s in INDEXES.split(";") if s.strip()):
        try:
            conn.execute(stmt)
        except sqlite3.IntegrityError as e:
            first_line = stmt.splitlines()[0]
            print(f"[db] index not created ({e}): {first_line}")
    conn.commit()

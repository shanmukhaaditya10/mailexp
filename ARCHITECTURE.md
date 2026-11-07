# mailexp — Architecture

A local, single-user tool for running cold-email outreach sequences to YC founders:
import lead lists from Excel, generate personalised drafts from templates, send via
Gmail SMTP, and track replies/bounces/OOO via Gmail IMAP.

There is **no external database, message broker, or scheduler**. State lives in a local
SQLite file (WAL mode), sends run in a single in-process Python thread, and the React UI
polls the FastAPI backend roughly every 1.2s. The backend is split into thin HTTP
`routes/` and business-logic `services/`.

---

## 1. High-level diagram

```
┌─────────────────────────────┐        ┌────────────────────────────────────────────┐
│  Browser (React + Vite)     │        │  FastAPI backend (uvicorn)                  │
│  web/  →  localhost:5173    │  HTTP  │  app/main.py  →  localhost:8000             │
│                             │ ─────► │  routes/*  →  services/*                    │
│  polls 8 endpoints / 1.2s   │  /api  │  ┌──────────────┐   ┌───────────────┐      │
│  (no websockets/SSE)        │ ◄───── │  │ SQLite (WAL) │   │ 1 sender      │      │
└─────────────────────────────┘        │  │  app/db.py   │   │  thread       │      │
                                       │  └──────┬───────┘   │ (sender.py)   │      │
                                       │         │           └──────┬────────┘      │
                                       └─────────┼──────────────────┼───────────────┘
                                                 │                  │
                                      ┌──────────▼───────┐  ┌───────▼──────────┐
                                      │ data/mailexp.db  │  │ Gmail             │
                                      │ data/resume.pdf  │  │  SMTP 465 (send)  │
                                      └──────────────────┘  │  IMAP (read)      │
                                                            └───────────────────┘
   *.xlsx (lead lists, project root) ──import──► SQLite
```

---

## 2. Tech stack & dependencies

### Backend — `requirements.txt`
| Package | Role |
|---|---|
| `fastapi` | HTTP API + routing |
| `uvicorn[standard]` | ASGI dev server |
| `python-dotenv` | load `.env` (Gmail creds) |
| `imapclient` | read Gmail **Sent Mail** and **INBOX** |
| `pydantic` | request body schemas |
| `python-multipart` | form/file handling |
| `openpyxl` | parse lead `.xlsx` files |
| `markdown` | email body markdown → HTML |
| `nh3` | allowlist HTML sanitisation of the rendered body |

Mail specifics: **sending** is stdlib `smtplib` over `SMTP_SSL smtp.gmail.com:465`;
**reading** is `imapclient` over SSL against `imap.gmail.com`. No OAuth — a Gmail
**App Password** is used (`GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `IMAP_HOST`).

### Frontend — `web/package.json`
| Package | Role |
|---|---|
| `react` / `react-dom` (19) | UI |
| `marked` (18) | client-side markdown preview in the editor |
| `vite` (8) + `@vitejs/plugin-react` | dev server + bundler |
| `oxlint` | lint (`npm run lint`) |

Vite proxies `/api` → `http://localhost:8000` (`web/vite.config.js`); the frontend uses
relative `/api/...` URLs (`web/src/api.js`).

---

## 3. Project file structure

```
mailexp/
├── app/                        # FastAPI backend
│   ├── main.py                 # app assembly, CORS, startup recovery, router mounts
│   ├── db.py                   # SQLite schema, migrations, indexes, WAL tuning, seeds
│   ├── import_leads.py         # xlsx → DB (upsert leads/founders, validation, reports)
│   ├── render.py               # {{var}} template rendering + name joining
│   ├── gmail_send.py           # MIME build, sanitise→HTML, resume attach, SMTP send
│   ├── gmail_read.py           # IMAP fetch/parse, Message-ID lookup, email extraction
│   ├── sanitize.py             # nh3 allowlist HTML cleaner
│   ├── util.py                 # email validation, header cleaning, send-window parsing
│   ├── sender.py               # in-memory background send-job queue (one thread)
│   ├── routes/                 # thin HTTP layer
│   │   ├── __init__.py         # service_error() → HTTPException mapping
│   │   ├── leads.py            # leads, lists, import, stats
│   │   ├── mailbox.py          # sync (sent/replies) + reply listing
│   │   ├── outreach.py         # queue, drafts, sending, outbox/review, templates
│   │   └── settings.py         # settings get/put
│   └── services/               # business logic
│       ├── drafts.py           # draft generation (build_draft)
│       ├── leads.py            # lead serialization + due_leads() eligibility
│       ├── mailbox_sync.py     # conservative matching, reply classification, sync
│       ├── sending.py          # THE core: claim, cap, window, intervals, lifecycle, reconcile
│       └── settings.py         # settings accessors
├── web/                        # React + Vite frontend
│   ├── src/                    # App.jsx, api.js, components/, lib/
│   ├── public/ · dist/         # static assets / build output
│   ├── vite.config.js          # /api → :8000 proxy
│   └── package.json
├── data/                       # runtime state (gitignored)
│   ├── mailexp.db              # SQLite (+ -wal / -shm)
│   └── resume.pdf              # default attachment
├── *.xlsx                      # lead lists (project root = importable lists)
├── requirements.txt
├── .env / .env.example
└── main.py                     # empty placeholder
```

**Run:** backend `uvicorn app.main:app --reload` (:8000), frontend `npm run dev` in `web/` (:5173).

---

## 4. Data model (SQLite — `app/db.py`)

File: `data/mailexp.db`. Connections use **WAL**, `busy_timeout=5000ms`,
`synchronous=NORMAL`, `foreign_keys=ON`. Each request/worker opens its own connection
(never shared across threads).

| Table | Purpose | Key columns |
|---|---|---|
| `leads` | one row per company | `company`, `domain`, `source` (list id), `status`, `step` (0–5), `last_sent_at`, `next_due_at`, `context` |
| `founders` | people at a lead | `lead_id`, `name`, `first_name`, `email`, `linkedin`, `twitter`, `invalid` (1 = known-bad address) |
| `messages` | each draft/send attempt | `lead_id`, `step`, `to_addrs`, `subject`, `body`, `status`, `message_id`, `client_message_id`, `thread_id`, `sent_at`, `send_attempts`, `last_error`, `last_attempt_at`, `attach_resume`, `manual` |
| `replies` | replies/bounces/OOO | `lead_id`, `thread_id`, `from_addr`, `message_id`, `snippet`, `received_at`, `kind`, `company`, `match_note` |
| `templates` | sequence steps 0–4 | `step`, `subject_tpl`, `body_tpl` |
| `settings` | key/value config | see §10 |

**Statuses:** leads `not_contacted → contacted → (replied | bounced | done)`.
Messages have an explicit lifecycle (see §7.4), including `send_unknown`/`send_failed`.

**Indexes** (`_apply_indexes`, created one-by-one; a failing UNIQUE index is logged and
skipped rather than crashing startup):
`leads(source)`, `leads(status, step, next_due_at)`, `leads(domain)`,
`founders(lead_id)`, `founders(email)`, `messages(lead_id)`, `messages(status)`,
`messages(status, sent_at)`, `messages(thread_id)`, `replies(lead_id)`,
`replies(received_at)`, plus **partial unique** indexes:
`messages(message_id)`, `messages(lead_id, step) WHERE status='sent' AND manual=0`,
`replies(message_id)`. Manually-sent messages (`manual=1`) are exempt from the step index so a
chosen template can be sent to a lead at any time — see §6.5.

**Migrations:** additive `ALTER TABLE` entries in `MIGRATIONS`, applied only when the
column is missing (`messages.manual` was added this way); `leads.source` is backfilled to the
default list stem for old DBs. The step unique index changed with `manual`, and SQLite cannot
alter a UNIQUE index in place, so `_rebuild_step_index()` drops the old one on startup and
`_apply_indexes` recreates it with the new predicate.

`init_db()` seeds 5 default templates (intro + 4 follow-ups) and default settings.

---

## 5. Backend layer split

- **`app/main.py`** — builds the `FastAPI` app, narrow CORS for the Vite dev origins,
  runs `init_db()` + `sending.recover_stale_sending()` on startup, mounts the four routers.
- **`routes/*`** — HTTP only: parse/validate request models, open a connection, call a
  service, map errors (`routes/__init__.py::service_error` uses an exception's `.status`).
- **`services/*`** — all business rules. This is where sending safety, matching, and
  eligibility logic lives; routes are intentionally thin.

---

## 6. End-to-end flows

### 6.1 Import a list
`POST /api/import?list=<stem>` → `import_leads.import_from_xlsx()`. Every real `*.xlsx`
in the project root is a selectable list; the filename stem is the `source`. Reads the
`Companies` sheet, requires a `Company` column, then **upserts** leads (keyed by
casefolded company, never overwriting user `context`/`status`) and founders (keyed by
`lead_id` + casefolded name). Emails are validated (`util.is_valid_email`); malformed ones
are dropped with a reported error. A workbook `Invalid` column (bounced / known-bad
addresses) is read into `founders.invalid`; the column is only touched when the sheet
actually has it, so lists without it never clear an existing flag. Returns
created/updated/duplicate/error counts.
`resolve_list()` blocks path traversal (only files directly in the project root).

### 6.2 Draft generation
`POST /api/leads/{id}/draft` (or `/api/queue/drafts`) → `drafts.build_draft()`:
pick template by lead `step` (or an explicit `template_id`), render `{{names}}`
(one name, or "A and B" for two, else `team`), `{{first_name}}` (same join as
`{{names}}`), `{{company}}`, `{{one_liner}}`; for step > 0 prefix the subject with
`Re:` if empty; then **replace** any existing draft and insert a fresh `status='draft'`
row in one transaction. Only *reachable* founders (an address present and not flagged
`invalid`) are used — for both the greeting and the `To:` list; a lead with no reachable
founder raises `DraftError` and is skipped.

### 6.3 Scheduling (there is no scheduler)
"Due" is derived state, computed in `leads.due_leads()`:
```sql
status NOT IN ('replied','bounced','done') AND step < 5
AND (next_due_at IS NULL OR next_due_at <= now)
```
`next_due_at` is written as `sent_at + step_gap_days` whenever a send succeeds, and set
`NULL` (leaving the queue) on reply/bounce. Nothing runs autonomously — the user opens
the app and clicks send/sync. `step_gap_days` (default 2) is the only cadence control.

### 6.4 Send lifecycle (`services/sending.py`) — the heart of the system
```
draft / dry_run / send_failed --claim--> sending
  sending --(dry run)-------> dry_run
  sending --(accepted)------> sent            (sequence cursor advances)
  sending --(hard failure)--> send_failed     (deliberate retry allowed)
  sending --(ambiguous)-----> send_unknown    (NEVER auto-retried; manual reconcile)
```
`send_message(conn, id, dry_run, session=None)`:
1. Reject if already `sent`/`sending`/`send_unknown`, or not a sendable status.
2. **Atomic claim** (`claim()`): one `UPDATE ... WHERE status IN (...) AND (<daily-reserved subquery>) < cap`. The eligibility check, cap check, and write are a single SQLite
   statement, so concurrent callers (double-click, duplicate enqueue) can never both win,
   nor collectively exceed the cap. Reserved count = messages with status in
   `('sent','sending','send_unknown')` dated today (in-flight/ambiguous consume a slot).
3. `_perform()`: reject if lead is `replied/bounced/done`; validate recipients; enforce the
   **send window** (`check_limits`, time-of-day only — the cap was already enforced in the
   claim); guard against re-sending an already-sent sequence step (app check + DB unique
   index backstop) — **skipped when the message is `manual`**; persist `client_message_id`
   before the SMTP attempt; send.
4. On success, message → `sent` and the lead sequence cursor advances to
   `min(max(current, message_step+1), 5)` (`_next_step`), with `last_sent_at`, `next_due_at`,
   `status='contacted'` set in one transaction. For an automatic send this is `current+1`;
   for a manual send it neither rewinds nor needlessly skips. A `sqlite3.IntegrityError`
   from the unique step index means "delivered but not recorded" → ambiguous (manual
   messages are exempt from that index, so they cannot hit this).
5. Failure handling: `SendSkipped` (cap/window/lead) **undoes the claim** and leaves the
   message sendable; `SendError` non-ambiguous → `send_failed`; ambiguous → `send_unknown`.

**SMTP acceptance and the SQLite write can't be atomic**, so `gmail_send.DeliveryError`
carries a `submitted` flag: `submitted=False` (connection/auth/`SMTPRecipientsRefused`…)
is a hard failure; `submitted=True` (timeout/disconnect mid-transaction) is ambiguous →
`send_unknown`, never auto-retried. There is no exactly-once guarantee over SMTP; the
design makes the common failure modes safe and never silently duplicates.

**Startup recovery** (`recover_stale_sending`): any message left in `sending` (process
died mid-attempt) becomes `send_unknown` for manual review — never silently resent.

**Manual review** (`/api/outbox`, `resolve`, `reconcile`, `retry`):
`reconcile_unknown()` searches Gmail **Sent Mail by the persisted Message-ID**;
`resolve_message(was_sent=True)` marks it sent and advances the lead; `was_sent=False`
makes it retryable (`send_failed`). `retry` always reconciles first and requires explicit
`{"confirm": true}` before resending an unconfirmed message.

### 6.5 Manual sends — "send a template anytime"
A message built from an **explicitly chosen template** (`build_draft(..., template_id=...)`)
is marked `manual=1`. Manual messages:
- bypass the one-per-sequence-step guard (app check **and** the DB unique index), so the same
  template can be sent to an already-contacted lead, at any time, including once the 5-step
  sequence is complete;
- stay exempt from the daily-cap reservation only in the sense that an explicit selection via
  `/api/queue/send` with `lead_ids` skips the *cap* pre-check (the send window still applies);
- still advance the lead's cursor via `_next_step` (never backwards).

The normal UI path is the **Leads tab**: select any leads (including `contacted`) and a
template, then "Send selected" → `POST /api/queue/send {lead_ids, template_id}`. The due-only
**Queue tab** is unchanged. The database backstop `idx_messages_sent_step` is scoped
`WHERE status='sent' AND manual=0` so automatic sends still refuse to re-send a step.

### 6.5 Sending — the job worker (`app/sender.py`)
Entry points funnel into `sender.enqueue(...)`:
- `POST /api/messages/{id}/send` → `enqueue([id])`
- `POST /api/queue/send` → applies the daily cap at enqueue time, builds/collects drafts,
  then enqueues (supports optional `lead_ids` selection and a forced `template_id`).

A **single daemon thread**, started lazily, drains an in-memory `_jobs` dict. Jobs are
**not persisted** (lost on restart; DB status columns are durable so interrupted sends can
be reviewed). Per message it calls `wait_for_interval()` (real sends only) then
`send_message()`, records per-message outcomes, supports cancellation, and stops the rest
of a batch on a cap/window skip (recording the reason). The UI polls `GET /api/send-jobs`.

### 6.6 Mailbox sync (`services/mailbox_sync.py`)
`POST /api/sync` = `sync_sent` + `sync_replies` (also callable separately). IMAP match
order is deliberately conservative:
1. exact Gmail **thread id** (`X-GM-THRID`),
2. exact **email address**,
3. **domain**, only when exactly one lead matches.

More than one candidate → left unmatched and recorded for review; a lead's sequence is
never stopped on an ambiguous match. Reply dedup uses the stable RFC **Message-ID**.

- `sync_sent`: record unseen sent mail, recompute each touched lead's step/next_due/status.
- `sync_replies`: classify `bounce` (mailer-daemon/undeliverable/delivery failure) vs
  `ooo` (Auto-Submitted / out-of-office / autoreply) vs `reply`. A `reply` sets lead
  `status='replied'`; a `bounce` sets `status='bounced'`; both clear `next_due_at`. OOO is
  recorded but does not stop the sequence.

### 6.7 Stats
`GET /api/stats` — counts of leads, founders, sent messages, replied leads (optional `?list=`).

---

## 7. Throughput & performance

This is a **low-volume, intentional outreach tool**, not a bulk mailer. Throughput is
bounded by three deliberate throttles plus a single-threaded worker.

### 7.1 The three rate limits (all in `settings`)
| Knob | Default | Effect | Enforced in |
|---|---|---|---|
| `daily_cap` | **40/day** | max real sends per local calendar day | atomically in `claim()` (counts `sent`+`sending`+`send_unknown` today) |
| `min_send_interval_seconds` | **2s** | minimum spacing between consecutive real sends | `wait_for_interval()` before each send (real sends only) |
| `send_window` | **09:00-18:00** | only send inside this local time-of-day window | `check_limits()` per send |

> Defaults are seeded on a fresh database and **migrated on startup**: a stored value that
> still equals a superseded default (`daily_cap` `25`; `min_send_interval_seconds` `20` or
> `1`) is advanced to the new one, while any value the user customised is left untouched
> (`db.SETTINGS_MIGRATIONS` / `_migrate_settings()`).

### 7.2 Throughput ceilings
- **Interval ceiling:** 2s spacing → `60 / 2 = 30 sends/min` → **1800 sends/hour**, if the
  interval were the only limit.
- **Daily ceiling:** `daily_cap = 40` → **40 real sends/day**. This is the *binding*
  constraint and is far below the interval ceiling.
- **Effective sustained throughput ≈ 40 real emails/day.** A full day's cap of 40 sends
  takes `(40−1) × 2s ≈ 78s` of pacing plus SMTP overhead, and — because the SMTP connection
  is reused across the batch — only one TLS/auth handshake, so the batch finishes in a
  couple of minutes and the account is idle the rest of the day.
- **Dry runs are not paced** (the interval wait is skipped) and send no SMTP traffic, so a
  dry-run batch is limited only by SQLite write speed — effectively instant.
- **Sequence throughput per lead:** with `step_gap_days = 2`, a lead advances one step
  every 2 days; the full 5-step sequence spans `4 × 2 = 8 days` minimum.

### 7.3 Concurrency model
- **One sender thread, fully sequential.** Messages within a batch are sent one at a time;
  a slow socket (60s SMTP timeout) stalls the whole batch. Dry runs share the same thread.
- **SMTP connection is reused per batch** — `sender._run` opens one
  `gmail_send.SmtpSession` for a real-send batch and threads it through
  `sending.send_message(..., session)` → `_perform` → `send_email(..., session=...)`, so the
  TLS+auth cost (~1–2s) is paid **once** instead of per email. The session connects lazily,
  is dropped after any ambiguous failure, and the next send reconnects; dry runs open no
  connection. A one-shot `SmtpSession` is still used when `send_email` is called without a
  session (e.g. ad-hoc API sends).
- **Two IMAP connections per `/api/sync`** (one for Sent, one for INBOX), each a fresh
  connect/login/logout cycle.
- **SQLite concurrency:** WAL lets the UI read while the worker writes; short writes
  serialise via `busy_timeout=5000ms`. The atomic-claim + partial-unique-index design makes
  concurrent claims safe without a lock service.

### 7.4 Frontend polling load
- `App.jsx` runs `setInterval(refresh, 1200)`; each `refresh()` fires **8 parallel API
  calls** (`lists, leads, queue, templates, replies, settings, stats, send-jobs`) →
  `8 / 1.2s ≈ 6.7 requests/s` steady-state, every one opening its own SQLite connection.
- After queuing a send, `awaitJobOutcome` polls `/api/send-jobs` every **500ms** (up to 12×)
  → +2 req/s while a job runs.
- This is backend/DB read load, not send throughput, but it is the dominant request rate
  in the system (the UI never uses websockets/SSE).

### 7.5 Sync throughput
- Each sync scans only the **last N messages** (`limit`, default 50) of Sent Mail and
  INBOX. `client.search(["ALL"])` fetches the full UID list and then slices the tail, so
  it is O(mailbox size) per call, and there is **no pagination** — mail older than the
  newest 50 is not revisited, so very active mailboxes can miss matches.

### 7.6 External provider limits (context)
Gmail's own limits sit above this app's: roughly **~500 recipients/day** for a free account
and ~2000/day for Workspace over SMTP. The app's default `daily_cap=40` is a self-imposed
safety margin well under those, which also keeps the account out of spam-throttling.

### 7.7 Bottleneck summary (most binding first)
1. `daily_cap` (40/day) — dominant, intentional.
2. `min_send_interval_seconds` (2s) — 30/min pacing ceiling.
3. Single sender thread — a slow socket stalls the batch (connection is reused per batch).
4. `send_window` — no sends outside 09:00–18:00.
5. IMAP full-mailbox `search(ALL)` per sync — scales with mailbox size, no incremental fetch.
6. UI 1.2s × 8-endpoint polling — steady backend read load.

---

## 8. API surface

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/leads` | list leads (`?list=`) |
| GET | `/api/leads/{id}` | lead + messages + replies |
| PUT | `/api/leads/{id}` | update `context` / `status` |
| GET | `/api/lists` | xlsx lists + per-list counts |
| POST | `/api/import` | import a list |
| GET | `/api/stats` | counters |
| POST | `/api/sync` · `/api/sync/sent` · `/api/sync/replies` | mailbox sync |
| GET | `/api/replies` | recent replies (`?list=`) |
| GET | `/api/queue` | leads due now |
| POST | `/api/leads/{id}/draft` · `/api/queue/drafts` | build draft(s) (optional selection/template) |
| GET/PUT | `/api/messages/{id}` | read/update a draft |
| POST | `/api/messages/{id}/send` · `/api/queue/send` | enqueue send(s) |
| GET | `/api/send-jobs` · POST `/api/send-jobs/{id}/cancel` | job status / cancel |
| GET | `/api/outbox` | messages awaiting manual review |
| POST | `/api/messages/{id}/resolve` · `/reconcile` · `/retry` | outbox reconciliation |
| GET/PUT | `/api/templates` `/{id}` | sequence templates |
| GET/PUT | `/api/settings` | config |

---

## 9. Frontend layout (`web/src/`)

| File | Role |
|---|---|
| `App.jsx` | Shell: sidebar + tabs, 1.2s `refresh()` poll, dry-run toggle, composer, toasts, job-outcome wait |
| `api.js` | Thin `fetch` wrapper + all endpoint bindings |
| `lib/format.js` | `namesOf`, `companyUrl`, `STATUS_LABEL` |
| `components/Sidebar.jsx` | List selector (All / per-xlsx) + per-list re-import button |
| `components/Leads.jsx` | Lead table + search + select-leads/send-template toolbar |
| `components/Queue.jsx` | Due leads, multi-select, template picker, send-all / send-selected |
| `components/Composer.jsx` | Review/edit a draft, edit context, attach toggle, send |
| `components/MarkdownEditor.jsx` | Markdown toolbar + live preview (`marked`) |
| `components/Templates.jsx` | Edit step templates |
| `components/Replies.jsx` | Recent replies/bounces list |
| `components/Settings.jsx` | Cap, window, interval, gap, dry-run, resume path, signature |
| `components/Stat.jsx` · `JobToast.jsx` | Header counters · background send progress + cancel |
| `App.css` / `index.css` | Styling |

No client cache, router, or global store: everything is refetched on an interval.

---

## 10. Config & secrets

- `.env` (gitignored): `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `IMAP_HOST`.
- `.env.example` documents the format.
- Runtime tunables live in the `settings` table and are edited from the Settings tab:
  `daily_cap` (40), `dry_run` (1), `resume_path`, `send_window` (09:00-18:00),
  `step_gap_days` (2), `min_send_interval_seconds` (2), `signature`.
- Resume attachment path defaults to `data/resume.pdf`.

---

## 11. Safety invariants

- **Never double-send:** atomic single-statement claim + partial unique indexes
  (`messages(message_id)`, `messages(lead_id, step) WHERE status='sent'`).
- **Never exceed the cap:** the cap is checked inside the same UPDATE as the claim, and
  in-flight/ambiguous messages consume a slot.
- **Never silently resend:** interrupted (`sending`) and ambiguous (`send_unknown`) work is
  parked for manual review, reconciled against Gmail Sent Mail by Message-ID.
- **Never inject headers:** subjects/recipients are cleaned (`util.clean_header`), bodies
  sanitised (`sanitize.py`, nh3 allowlist).
- **Never resolve ambiguity destructively:** multiple-candidate mailbox matches are left
  unmatched and recorded, not guessed.

---

## 12. Notable characteristics / gaps

- **Jobs are in-memory** — send progress/job list is lost on restart; DB status is durable.
- **No autonomous scheduling** — nothing sends or syncs unless the user acts.
- **No OAuth** — Gmail App Password (assumes 2FA).
- **Sync is tail-only** — newest 50 messages per folder, no incremental/UID pagination.
- **Single-threaded sender** — one slow send blocks the batch; the SMTP connection is reused
  per batch but not pooled/parallelised.
- **Single-user, local-only** — SQLite, in-process threads, broad-but-explicit dev CORS.

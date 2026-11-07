"""FastAPI application assembly.

The app object, CORS policy, startup migration, and router registration live
here. Endpoint logic lives in `app/routes/*` and `app/services/*`.
"""
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import ROOT, get_conn, init_db
from .routes import leads, mailbox, outreach
from .routes import settings as settings_routes
from .services import sending

load_dotenv(ROOT / ".env")

app = FastAPI(title="mailexp")

# Local-only UI. Kept explicit and narrow on purpose.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    init_db()
    conn = get_conn()
    try:
        # A message left 'sending' means the process died mid-attempt; we can't
        # know if Gmail accepted it, so park it for manual review instead of
        # silently resending.
        recovered = sending.recover_stale_sending(conn)
        if recovered:
            print(f"[startup] {recovered} interrupted send(s) moved to review")
    finally:
        conn.close()


app.include_router(leads.router)
app.include_router(outreach.router)
app.include_router(mailbox.router)
app.include_router(settings_routes.router)

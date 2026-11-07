"""Application settings (key/value store plus a few derived flags)."""
from fastapi import APIRouter
from pydantic import BaseModel

from ..db import ROOT
from ..services import settings as settings_svc

router = APIRouter(prefix="/api", tags=["settings"])


class SettingsUpdate(BaseModel):
    daily_cap: str | None = None
    dry_run: str | None = None
    resume_path: str | None = None
    step_gap_days: str | None = None
    send_window: str | None = None
    min_send_interval_seconds: str | None = None
    signature: str | None = None


@router.get("/settings")
def get_settings():
    d = settings_svc.all_settings()
    # Whether credentials are present, without ever exposing their values.
    d["_gmail_configured"] = bool((ROOT / ".env").exists())
    return d


@router.put("/settings")
def update_settings(payload: SettingsUpdate):
    settings_svc.set_settings(payload.model_dump(exclude_none=True))
    return settings_svc.all_settings()

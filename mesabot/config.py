"""All configuration comes from environment variables. Nothing secret lives in code."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Courses that never take attendance. They are shown as "attendance not tracked"
# and never raise an attendance alert. Matching ignores case and punctuation,
# so "Mesa Start-up Lab" and "Mesa Startup Lab" are the same course.
DEFAULT_UNTRACKED_COURSES = (
    "AI Workshops",
    "Career Readiness",
    "Workshops",
    "Mesa Startup Lab",
)


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _flag(name: str, default: bool) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    # Required secrets
    nexus_token: str | None = None
    nexus_base_url: str | None = None
    callmebot_phone: str | None = None
    callmebot_api_key: str | None = None
    google_credentials_json: str | None = None

    # Optional settings
    google_calendar_ids: tuple[str, ...] = ("primary",)
    data_dir: str = "./data"
    webhook_secret: str | None = None
    dry_run: bool = False
    run_scheduler: bool = True
    attendance_threshold: float = 75.0
    at_risk_margin: int = 1
    action_lookback_days: int = 7
    untracked_courses: tuple[str, ...] = field(default=DEFAULT_UNTRACKED_COURSES)
    whatsapp_chunk_size: int = 1400

    @classmethod
    def from_env(cls) -> "Config":
        # Railway sets RAILWAY_VOLUME_MOUNT_PATH when a volume is attached.
        data_dir = _env("DATA_DIR") or _env("RAILWAY_VOLUME_MOUNT_PATH") or "./data"
        calendars = _env("GOOGLE_CALENDAR_ID", "primary") or "primary"
        untracked = _env("ATTENDANCE_UNTRACKED_COURSES")
        return cls(
            nexus_token=_env("NEXUS_MCP_TOKEN"),
            nexus_base_url=_env("NEXUS_MCP_BASE_URL"),
            callmebot_phone=_env("CALLMEBOT_PHONE"),
            callmebot_api_key=_env("CALLMEBOT_API_KEY"),
            google_credentials_json=_env("GOOGLE_CALENDAR_CREDENTIALS_JSON"),
            google_calendar_ids=tuple(c.strip() for c in calendars.split(",") if c.strip()),
            data_dir=data_dir,
            webhook_secret=_env("WEBHOOK_SECRET"),
            dry_run=_flag("DRY_RUN", False),
            run_scheduler=_flag("RUN_SCHEDULER", True),
            attendance_threshold=float(_env("ATTENDANCE_THRESHOLD", "75")),
            at_risk_margin=int(_env("ATTENDANCE_AT_RISK_MARGIN", "1")),
            action_lookback_days=int(_env("ACTION_LOOKBACK_DAYS", "7")),
            untracked_courses=(
                tuple(c.strip() for c in untracked.split(",") if c.strip())
                if untracked
                else DEFAULT_UNTRACKED_COURSES
            ),
            whatsapp_chunk_size=int(_env("WHATSAPP_CHUNK_SIZE", "1400")),
        )

    def missing(self) -> list[str]:
        """Names of required variables that are not set (Google Calendar is optional)."""
        required = {
            "NEXUS_MCP_TOKEN": self.nexus_token,
            "NEXUS_MCP_BASE_URL": self.nexus_base_url,
            "CALLMEBOT_PHONE": self.callmebot_phone,
            "CALLMEBOT_API_KEY": self.callmebot_api_key,
        }
        return [name for name, value in required.items() if not value]

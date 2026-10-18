"""Google Calendar (read-only) via google-auth + googleapiclient.

GOOGLE_CALENDAR_CREDENTIALS_JSON holds either
  * a service-account key (type "service_account"): share your calendar with the
    service account's email and set GOOGLE_CALENDAR_ID to your calendar id
    (usually your Gmail address), or
  * an authorized-user token (type "authorized_user": client_id, client_secret,
    refresh_token), e.g. from `python -m mesabot.cli google-auth`.
The value may be the JSON itself, base64 of the JSON, or a path to a file.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime
from typing import Any

from .timeutil import IST, iso, parse_iso

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def load_credentials_info(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    if raw.startswith("{"):
        return json.loads(raw)
    if os.path.exists(raw):
        with open(raw, encoding="utf-8") as fh:
            return json.load(fh)
    return json.loads(base64.b64decode(raw).decode("utf-8"))


class GoogleCalendar:
    def __init__(self, credentials_json: str, calendar_ids: tuple[str, ...] = ("primary",)):
        from google.oauth2 import credentials as user_credentials
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        info = load_credentials_info(credentials_json)
        if info.get("type") == "service_account":
            creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        else:
            creds = user_credentials.Credentials.from_authorized_user_info(info, scopes=SCOPES)
        self.calendar_ids = calendar_ids
        self.service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    def events(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for calendar_id in self.calendar_ids:
            page_token = None
            while True:
                resp = self.service.events().list(
                    calendarId=calendar_id, timeMin=iso(start), timeMax=iso(end),
                    singleEvents=True, orderBy="startTime", maxResults=250,
                    pageToken=page_token, timeZone="Asia/Kolkata",
                ).execute()
                for ev in resp.get("items", []):
                    if ev.get("status") == "cancelled":
                        continue
                    results.append(normalize_event(ev, calendar_id))
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break
        return sorted(results, key=lambda e: e["start"])


def normalize_event(ev: dict[str, Any], calendar_id: str) -> dict[str, Any]:
    start_raw = ev.get("start", {})
    end_raw = ev.get("end", {})
    all_day = "date" in start_raw and "dateTime" not in start_raw
    start = parse_iso(start_raw.get("dateTime") or start_raw.get("date"))
    end = parse_iso(end_raw.get("dateTime") or end_raw.get("date"))
    return {
        "id": ev.get("id"),
        "title": ev.get("summary") or "(no title)",
        "start": start or datetime.now(IST),
        "end": end,
        "all_day": all_day,
        "location": ev.get("location"),
        "link": ev.get("hangoutLink") or ev.get("htmlLink"),
        "calendar": calendar_id,
    }


def build_calendar(credentials_json: str | None, calendar_ids: tuple[str, ...]) -> GoogleCalendar | None:
    if not credentials_json:
        return None
    try:
        return GoogleCalendar(credentials_json, calendar_ids)
    except Exception as exc:  # bad JSON or missing library: run without the calendar
        log.error("Google Calendar disabled: %s", exc)
        return None

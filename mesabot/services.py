"""Wires config → Nexus client, JSON log, WhatsApp sender and Google Calendar."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from .config import Config
from .gcal import GoogleCalendar, build_calendar
from .nexus import Nexus, build_client
from .store import Store
from .whatsapp import CallMeBot

log = logging.getLogger(__name__)


@dataclass
class Services:
    config: Config
    nexus: Nexus
    store: Store
    sender: CallMeBot
    gcal: GoogleCalendar | None
    fixed_now: datetime | None = None  # previews and tests pin the clock

    @classmethod
    def from_config(cls, config: Config, fixtures: str | None = None,
                    now: datetime | None = None, store: Store | None = None,
                    sender: CallMeBot | None = None, gcal: GoogleCalendar | None = None) -> "Services":
        missing = config.missing()
        if missing and not fixtures:
            log.warning("Missing environment variables: %s", ", ".join(missing))
        client = build_client(config.nexus_base_url, config.nexus_token, fixtures=fixtures, now=now)
        return cls(
            config=config,
            nexus=Nexus(client),
            store=store or Store.in_dir(config.data_dir),
            sender=sender or CallMeBot(config.callmebot_phone, config.callmebot_api_key,
                                       chunk_size=config.whatsapp_chunk_size,
                                       dry_run=config.dry_run or bool(fixtures)),
            gcal=gcal if gcal is not None or fixtures else build_calendar(
                config.google_credentials_json, config.google_calendar_ids),
            fixed_now=now,
        )

"""Wires config → Nexus client, JSON log, message sender (Telegram or CallMeBot) and Google Calendar."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from .config import Config
from .gcal import GoogleCalendar, build_calendar
from .nexus import Nexus, build_client
from .store import Store
from .telegram import Telegram
from .whatsapp import CallMeBot

log = logging.getLogger(__name__)


@dataclass
class Services:
    config: Config
    nexus: Nexus
    store: Store
    sender: Telegram | CallMeBot
    gcal: GoogleCalendar | None
    fixed_now: datetime | None = None  # previews and tests pin the clock

    @classmethod
    def from_config(cls, config: Config, fixtures: str | None = None,
                    now: datetime | None = None, store: Store | None = None,
                    sender: Telegram | CallMeBot | None = None,
                    gcal: GoogleCalendar | None = None) -> "Services":
        missing = config.missing()
        if missing and not fixtures:
            log.warning("Missing environment variables: %s", ", ".join(missing))
        client = build_client(config.nexus_base_url, config.nexus_token, fixtures=fixtures, now=now)
        store = store or Store.in_dir(config.data_dir)
        dry_run = config.dry_run or bool(fixtures)
        if sender is None and config.channel == "telegram":
            sender = Telegram(config.telegram_bot_token or "",
                              lambda: config.telegram_chat_id or store.meta("telegram_chat_id"),
                              dry_run=dry_run)
        return cls(
            config=config,
            nexus=Nexus(client),
            store=store,
            sender=sender or CallMeBot(config.callmebot_phone, config.callmebot_api_key,
                                       chunk_size=config.whatsapp_chunk_size, dry_run=dry_run),
            gcal=gcal if gcal is not None or fixtures else build_calendar(
                config.google_credentials_json, config.google_calendar_ids),
            fixed_now=now,
        )

"""Outbound WhatsApp via CallMeBot (https://www.callmebot.com/blog/free-api-whatsapp-messages/).

CallMeBot is send-only: it can message *you*, but it does not forward your replies.
Inbound messages reach the bot through the Flask webhook (see app.py and README).
"""

from __future__ import annotations

import logging
import time
from typing import Callable

import requests

log = logging.getLogger(__name__)

CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"


def chunk_message(text: str, size: int) -> list[str]:
    """Split on blank lines, then lines, so a section is never cut mid-line."""
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= size:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(block) > size:
            cut = block.rfind("\n", 0, size)
            cut = cut if cut > 0 else size
            chunks.append(block[:cut])
            block = block[cut:].lstrip("\n")
        current = block
    if current:
        chunks.append(current)
    if len(chunks) > 1:
        chunks = [f"{c}\n_({i}/{len(chunks)})_" for i, c in enumerate(chunks, 1)]
    return chunks


class CallMeBot:
    def __init__(self, phone: str | None, api_key: str | None, chunk_size: int = 1400,
                 dry_run: bool = False, pause_seconds: float = 2.0,
                 printer: Callable[[str], None] | None = None):
        self.phone = (phone or "").replace(" ", "")
        self.api_key = api_key or ""
        self.chunk_size = chunk_size
        self.dry_run = dry_run or not (self.phone and self.api_key)
        self.pause_seconds = pause_seconds
        self.printer = printer or (lambda s: log.info("DRY RUN WhatsApp →\n%s", s))
        self.sent: list[str] = []

    def send(self, text: str) -> bool:
        ok = True
        chunks = chunk_message(text, self.chunk_size)
        for i, chunk in enumerate(chunks):
            self.sent.append(chunk)
            if self.dry_run:
                self.printer(chunk)
                continue
            if i:
                time.sleep(self.pause_seconds)  # CallMeBot rate-limits bursts
            ok = self._send_one(chunk) and ok
        return ok

    def _send_one(self, chunk: str) -> bool:
        params = {"phone": self.phone, "text": chunk, "apikey": self.api_key}
        for attempt in range(3):
            try:
                resp = requests.get(CALLMEBOT_URL, params=params, timeout=30)
                body = resp.text.lower()
                if resp.ok and "error" not in body[:200]:
                    return True
                log.warning("CallMeBot %s: %s", resp.status_code, resp.text[:300])
            except requests.RequestException as exc:
                log.warning("CallMeBot request failed: %s", exc)
            time.sleep(2 ** (attempt + 1))
        log.error("CallMeBot: giving up on a message chunk")
        return False

"""Telegram: outbound messages and the inbound chat (long polling, no public URL needed).

Setup: create a bot with @BotFather, set TELEGRAM_BOT_TOKEN, deploy, then send the bot
any message. The first chat to message it becomes the owner (stored in the log), unless
TELEGRAM_CHAT_ID pins the owner explicitly. Every other chat is ignored.
"""

from __future__ import annotations

import html
import logging
import re
import threading
import time
from typing import Any, Callable

import requests

from .whatsapp import chunk_message

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
TELEGRAM_LIMIT = 3800  # hard limit is 4096; leave room for HTML tags

BOT_COMMANDS = [
    ("due", "Due today and this week"),
    ("today", "Today's timetable and action items"),
    ("week", "The week ahead"),
    ("attendance", "Attendance % with danger flags"),
    ("grades", "Released grades"),
    ("pending", "Everything not submitted or not done"),
    ("mrs", "This week's MRS module status"),
    ("schedule", "Timetable for a day, e.g. /schedule monday"),
    ("done", "Mark something done, e.g. /done mrs"),
    ("help", "All commands"),
]

_URL_RE = re.compile(r"https?://\S+")
_BOLD_RE = re.compile(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])")
_ITALIC_RE = re.compile(r"(?<![\w_])_(?!\s)([^_\n]+?)(?<!\s)_(?![\w_])")
_STRIKE_RE = re.compile(r"(?<![\w~])~(?!\s)([^~\n]+?)(?<!\s)~(?![\w~])")


def to_html(text: str) -> str:
    """WhatsApp-style *bold* _italic_ ~strike~ → Telegram HTML. URLs and file names
    with underscores (Super_Case.pdf) are left untouched."""
    out, last = [], 0
    for match in _URL_RE.finditer(text):
        out.append(_format(text[last:match.start()]))
        out.append(html.escape(match.group(0)))
        last = match.end()
    out.append(_format(text[last:]))
    return "".join(out)


def _format(segment: str) -> str:
    s = html.escape(segment)
    s = _BOLD_RE.sub(r"<b>\1</b>", s)
    s = _ITALIC_RE.sub(r"<i>\1</i>", s)
    return _STRIKE_RE.sub(r"<s>\1</s>", s)


def strip_markup(text: str) -> str:
    return _STRIKE_RE.sub(r"\1", _ITALIC_RE.sub(r"\1", _BOLD_RE.sub(r"\1", text)))


class Telegram:
    """Same interface as CallMeBot (send / dry_run / printer / sent) plus polling."""

    def __init__(self, token: str, chat_id_getter: Callable[[], str | None],
                 dry_run: bool = False, printer: Callable[[str], None] | None = None):
        self.token = token
        self.chat_id_getter = chat_id_getter
        self.dry_run = dry_run
        self.printer = printer or (lambda s: log.info("DRY RUN Telegram →\n%s", s))
        self.sent: list[str] = []
        self._http = requests.Session()

    def api(self, method: str, **params: Any) -> Any:
        resp = self._http.post(API.format(token=self.token, method=method), json=params,
                               timeout=params.get("timeout", 0) + 30)
        data = resp.json() if resp.content else {}
        if not data.get("ok"):
            # Never include the URL in errors: it contains the bot token.
            raise RuntimeError(f"Telegram {method}: {data.get('description', resp.status_code)}")
        return data.get("result")

    # ---- outbound ------------------------------------------------------------------------

    def send(self, text: str, chat_id: str | None = None) -> bool:
        chat_id = chat_id or self.chat_id_getter()
        ok = True
        for chunk in chunk_message(text, TELEGRAM_LIMIT):
            self.sent.append(chunk)
            if self.dry_run:
                self.printer(chunk)
                continue
            if not chat_id:
                log.warning("No Telegram owner yet: send your bot a message first. Not sent:\n%s", chunk[:200])
                ok = False
                continue
            ok = self._send_one(chat_id, chunk) and ok
        return ok

    def _send_one(self, chat_id: str, chunk: str) -> bool:
        for attempt in range(3):
            try:
                self.api("sendMessage", chat_id=chat_id, text=to_html(chunk), parse_mode="HTML",
                         disable_web_page_preview=True)
                return True
            except RuntimeError as exc:
                if "parse" in str(exc).lower():  # bad markup: send it plain instead
                    try:
                        self.api("sendMessage", chat_id=chat_id, text=strip_markup(chunk),
                                 disable_web_page_preview=True)
                        return True
                    except (RuntimeError, requests.RequestException) as exc2:
                        log.warning("Telegram plain send failed: %s", exc2)
                else:
                    log.warning("Telegram send failed: %s", exc)
            except requests.RequestException as exc:
                log.warning("Telegram request failed: %s", type(exc).__name__)
            time.sleep(2 ** (attempt + 1))
        return False


def command_text(text: str) -> str:
    """'/schedule@MesaBot monday' → 'schedule monday'; '/start' → 'help'."""
    text = (text or "").strip()
    if text.startswith("/"):
        head, _, rest = text[1:].partition(" ")
        head = head.split("@", 1)[0].lower()
        if head == "start":
            head = "help"
        text = f"{head} {rest}".strip()
    return text


class TelegramPoller:
    """Long-polls getUpdates in a background thread and answers the owner's messages."""

    def __init__(self, bot: Telegram, store, handler: Callable[[str], str],
                 pinned_chat_id: str | None = None):
        self.bot = bot
        self.store = store
        self.handler = handler
        self.pinned_chat_id = pinned_chat_id
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None

    def owner(self) -> str | None:
        return self.pinned_chat_id or self.store.meta("telegram_chat_id")

    def start(self) -> None:
        try:
            self.bot.api("deleteWebhook")  # polling and webhooks are mutually exclusive
            self.bot.api("setMyCommands", commands=[{"command": c, "description": d} for c, d in BOT_COMMANDS])
        except Exception as exc:  # noqa: BLE001
            log.error("Telegram setup failed: %s", exc)
        self.thread = threading.Thread(target=self._run, name="telegram-poller", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        backoff = 2
        while not self._stop.is_set():
            try:
                self.poll_once(timeout=50)
                backoff = 2
            except Exception as exc:  # noqa: BLE001
                log.warning("Telegram polling error: %s", exc)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def poll_once(self, timeout: int = 0) -> int:
        offset = self.store.meta("telegram_offset")
        params: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset:
            params["offset"] = offset
        updates = self.bot.api("getUpdates", **params) or []
        for update in updates:
            self.store.set_meta("telegram_offset", update["update_id"] + 1)
            self.handle_update(update)
        return len(updates)

    def handle_update(self, update: dict[str, Any]) -> None:
        msg = update.get("message") or {}
        chat_id = str((msg.get("chat") or {}).get("id", ""))
        text = msg.get("text")
        if not chat_id or not text:
            return
        owner = self.owner()
        if owner is None and (msg.get("chat") or {}).get("type") == "private":
            self.store.set_meta("telegram_chat_id", chat_id)
            owner = chat_id
            self.bot.send("✅ Connected. This chat will get your Mesa briefs and alerts.\n"
                          "To lock it, set TELEGRAM_CHAT_ID=" + chat_id + " in Railway.", chat_id=chat_id)
        if chat_id != owner:
            log.warning("ignoring Telegram message from chat %s", chat_id)
            return
        try:
            reply = self.handler(command_text(text))
        except Exception as exc:  # noqa: BLE001
            log.exception("handler failed")
            reply = f"⚠️ Something went wrong ({type(exc).__name__}). Try again in a minute."
        self.bot.send(reply, chat_id=chat_id)

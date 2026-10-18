import dataclasses

from mesabot.bot import handle_message
from mesabot.config import Config
from mesabot.telegram import Telegram, TelegramPoller, command_text, to_html


def test_to_html_keeps_urls_and_filenames():
    text = "📊 *New grade* _(released Sun)_\n📎 Super_Case.pdf\n🔗 https://forms.gle/a_b*c\n~gone~ 5 < 6"
    out = to_html(text)
    assert "<b>New grade</b>" in out and "<i>(released Sun)</i>" in out
    assert "Super_Case.pdf" in out and "https://forms.gle/a_b*c" in out
    assert "<s>gone</s>" in out and "5 &lt; 6" in out


def test_command_text():
    assert command_text("/start") == "help"
    assert command_text("/schedule@MesaBot monday") == "schedule monday"
    assert command_text("/done mrs") == "done mrs"
    assert command_text("when is my next SQL class") == "when is my next SQL class"


class FakeTelegram(Telegram):
    def __init__(self, store, pinned=None):
        super().__init__("123:abc", lambda: pinned or store.meta("telegram_chat_id"))
        self.calls = []
        self.updates = []

    def api(self, method, **params):
        self.calls.append((method, params))
        if method == "getUpdates":
            out, self.updates = self.updates, []
            return out
        return {"message_id": 1}


def update(uid, chat_id, text, chat_type="private"):
    return {"update_id": uid, "message": {"chat": {"id": chat_id, "type": chat_type}, "text": text}}


def test_first_chat_becomes_owner_and_strangers_are_ignored(make_svc):
    svc = make_svc("2026-10-19T19:00:00+05:30")
    bot = FakeTelegram(svc.store)
    poller = TelegramPoller(bot, svc.store, lambda t: handle_message(svc, t))
    bot.updates = [update(10, 111, "/mrs"), update(11, 999, "grades")]
    assert poller.poll_once() == 2
    assert svc.store.meta("telegram_chat_id") == "111"
    assert svc.store.meta("telegram_offset") == 12
    sends = [p for m, p in bot.calls if m == "sendMessage"]
    assert all(p["chat_id"] == "111" for p in sends)
    assert "Connected" in sends[0]["text"] and "Resume Stress Test" in sends[1]["text"]
    assert sends[1]["parse_mode"] == "HTML"
    # Scheduled jobs now reach the owner.
    bot.send("hello")
    assert bot.calls[-1][1]["chat_id"] == "111"


def test_pinned_chat_id_wins(make_svc):
    svc = make_svc()
    bot = FakeTelegram(svc.store, pinned="222")
    poller = TelegramPoller(bot, svc.store, lambda t: handle_message(svc, t), pinned_chat_id="222")
    bot.updates = [update(1, 111, "due")]
    poller.poll_once()
    assert not [c for c in bot.calls if c[0] == "sendMessage"]
    assert svc.store.meta("telegram_chat_id") is None


def test_config_channel_and_missing():
    cfg = dataclasses.replace(Config(), nexus_token="t", telegram_bot_token="123:abc")
    assert cfg.channel == "telegram" and cfg.missing() == []
    assert Config().missing() == ["NEXUS_MCP_TOKEN", "TELEGRAM_BOT_TOKEN"]

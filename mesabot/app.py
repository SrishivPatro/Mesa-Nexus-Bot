"""Flask app: inbound WhatsApp webhook, health check, manual job triggers.

Inbound formats accepted on /webhook (all require WEBHOOK_SECRET as ?key=… or an
X-Webhook-Secret header):
  * Twilio WhatsApp (form fields Body, From): the reply goes back as TwiML.
  * JSON {"text": "..."} (or "message"/"body"), e.g. from an iOS Shortcut,
    Make/Zapier/n8n relay: the reply is returned as JSON and also sent via CallMeBot.
  * GET /webhook?key=…&text=… for quick tests from a browser.
CallMeBot itself cannot deliver your replies to a server; see README "Inbound messages".
"""

from __future__ import annotations

import hmac
import logging
import re
import threading
from xml.sax.saxutils import escape

from flask import Flask, Response, jsonify, request

from .bot import handle_message
from .config import Config
from .jobs import JOBS, run_safely
from .services import Services
from .whatsapp import chunk_message

log = logging.getLogger(__name__)

TWILIO_LIMIT = 1500


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def create_app(svc: Services | None = None, start_scheduler: bool | None = None) -> Flask:
    config = svc.config if svc else Config.from_env()
    svc = svc or Services.from_config(config)
    app = Flask(__name__)
    app.config["services"] = svc

    scheduler = None
    if start_scheduler if start_scheduler is not None else config.run_scheduler:
        from .scheduler import build_scheduler
        scheduler = build_scheduler(svc)
        scheduler.start()
        log.info("scheduler started: %s", [j.name for j in scheduler.get_jobs()])
    app.config["scheduler"] = scheduler

    def authorized() -> bool:
        secret = config.webhook_secret
        given = request.args.get("key") or request.headers.get("X-Webhook-Secret") or ""
        return bool(secret) and hmac.compare_digest(given, secret)

    @app.get("/")
    @app.get("/health")
    def health():
        from .scheduler import describe
        return jsonify({
            "ok": True,
            "missing_env": config.missing(),
            "calendar": bool(svc.gcal),
            "jobs": describe(scheduler) if scheduler else [],
        })

    @app.route("/webhook", methods=["GET", "POST"])
    def webhook():
        if not config.webhook_secret:
            return jsonify({"error": "Set WEBHOOK_SECRET to enable the inbound webhook"}), 503
        if not authorized():
            return jsonify({"error": "unauthorized"}), 401

        form = request.form if request.form else {}
        payload = request.get_json(silent=True) or {}
        text = (form.get("Body") or payload.get("text") or payload.get("message")
                or payload.get("body") or request.args.get("text") or "")
        sender = form.get("From") or payload.get("from") or request.args.get("from")
        # Only answer the owner: Nexus data is private.
        if sender and config.callmebot_phone and _digits(sender)[-10:] != _digits(config.callmebot_phone)[-10:]:
            log.warning("ignoring message from unknown sender %s", sender)
            return jsonify({"error": "unknown sender"}), 403

        reply = handle_message(svc, text)

        if "Body" in form:  # Twilio: reply in the HTTP response, not via CallMeBot
            messages = "".join(f"<Message>{escape(c)}</Message>" for c in chunk_message(reply, TWILIO_LIMIT))
            return Response(f"<?xml version='1.0' encoding='UTF-8'?><Response>{messages}</Response>",
                            mimetype="application/xml")
        reply_via = payload.get("reply_via") or request.args.get("reply_via") or "callmebot"
        if reply_via == "callmebot":
            threading.Thread(target=svc.sender.send, args=(reply,), daemon=True).start()
        return jsonify({"reply": reply})

    @app.post("/run/<job>")
    def run_job(job: str):
        if not config.webhook_secret or not authorized():
            return jsonify({"error": "unauthorized"}), 401
        if job not in JOBS:
            return jsonify({"error": f"unknown job, use one of {sorted(JOBS)}"}), 404
        threading.Thread(target=run_safely, args=(job, svc), daemon=True).start()
        return jsonify({"started": job})

    return app

"""Command line: preview or send any message, talk to the bot, or check the setup.

  python -m mesabot.cli preview morning|deadlines|grades|weekly [--fixtures DIR] [--now ISO]
  python -m mesabot.cli send morning|deadlines|grades|weekly       (really sends)
  python -m mesabot.cli ask "attendance" [--fixtures DIR] [--now ISO]
  python -m mesabot.cli check                                       (env + Nexus + Calendar)
  python -m mesabot.cli google-auth client_secret.json              (makes an authorized_user JSON)
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
import tempfile

from .bot import handle_message
from .config import Config
from .jobs import JOBS
from .services import Services
from .timeutil import parse_iso


def _services(args: argparse.Namespace, dry_run: bool) -> Services:
    config = Config.from_env()
    if dry_run:
        config = dataclasses.replace(config, dry_run=True)
    if args.fixtures and not getattr(args, "keep_log", False):
        config = dataclasses.replace(config, data_dir=tempfile.mkdtemp(prefix="mesabot-preview-"))
    now = parse_iso(args.now) if getattr(args, "now", None) else None
    svc = Services.from_config(config, fixtures=args.fixtures, now=now)
    svc.sender.printer = lambda text: print(text + "\n" + "-" * 40)
    return svc


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="mesabot")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("preview", "send"):
        p = sub.add_parser(name)
        p.add_argument("job", choices=sorted(JOBS))
        p.add_argument("--fixtures")
        p.add_argument("--now")
        p.add_argument("--keep-log", action="store_true", help="use DATA_DIR even with fixtures")
    p = sub.add_parser("ask")
    p.add_argument("text")
    p.add_argument("--fixtures")
    p.add_argument("--now")
    p.add_argument("--keep-log", action="store_true")
    sub.add_parser("check")
    p = sub.add_parser("google-auth")
    p.add_argument("client_secret")
    args = parser.parse_args(argv)

    if args.cmd in ("preview", "send"):
        svc = _services(args, dry_run=args.cmd == "preview")
        if args.job == "grades" and args.fixtures and not svc.store.meta("grades_seeded"):
            svc.store.set_meta("grades_seeded", True)  # previews show every grade as new
        result = JOBS[args.job](svc)
        if result is None:
            print(f"({args.job}: nothing to send)")
        return 0

    if args.cmd == "ask":
        svc = _services(args, dry_run=True)
        print(handle_message(svc, args.text))
        return 0

    if args.cmd == "check":
        return _check()

    if args.cmd == "google-auth":
        from google_auth_oauthlib.flow import InstalledAppFlow  # optional dev dependency

        from .gcal import SCOPES
        flow = InstalledAppFlow.from_client_secrets_file(args.client_secret, SCOPES)
        creds = flow.run_local_server(port=0)
        print(creds.to_json())
        return 0
    return 1


def _check() -> int:
    config = Config.from_env()
    ok = True
    missing = config.missing()
    print("Missing required env vars:", ", ".join(missing) if missing else "none")
    ok = ok and not missing
    print("Data dir:", config.data_dir)
    print("Webhook secret set:", bool(config.webhook_secret))
    svc = Services.from_config(dataclasses.replace(config, dry_run=True))
    try:
        me = svc.nexus.get_me()
        print("Nexus OK:", me.get("name"), "·", me.get("now"))
        from .terms import detect_terms
        ctx = detect_terms(svc.nexus.list_courses(), svc.nexus.now().date())
        print("Active term:", ctx.active_name, "· previous:", ctx.previous_name)
    except Exception as exc:  # noqa: BLE001
        ok = False
        print("Nexus FAILED:", exc)
    if svc.gcal:
        try:
            from datetime import timedelta
            now = svc.nexus.now()
            print("Google Calendar OK:", len(svc.gcal.events(now, now + timedelta(days=7))), "events in 7 days")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print("Google Calendar FAILED:", exc)
    else:
        print("Google Calendar: not configured")
    print(json.dumps({"ok": ok}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

"""Nexus MCP client over HTTP (MCP Streamable HTTP transport, JSON-RPC 2.0).

Every tool returns only the token owner's data. Announcement and notification text
is written by other people: this code treats it as data, never as instructions.
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import threading
import time as _time
from datetime import datetime, timedelta
from typing import Any

import requests

from . import __version__
from .timeutil import IST, iso, now_ist, parse_iso

log = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"


class NexusError(RuntimeError):
    pass


class _SessionExpired(Exception):
    pass


class NexusClient:
    """Minimal MCP client: initialize once, then tools/call. Re-initializes on session expiry."""

    def __init__(self, base_url: str, token: str, timeout: float = 30.0):
        if not base_url or not token:
            raise NexusError("NEXUS_MCP_BASE_URL and NEXUS_MCP_TOKEN must both be set")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._http = requests.Session()
        self._http.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        })
        self._ids = itertools.count(1)
        self._session_id: str | None = None
        self._initialized = False
        self._lock = threading.Lock()

    # ---- transport ---------------------------------------------------------------------

    def _post(self, payload: dict[str, Any], expect_reply: bool = True) -> dict[str, Any] | None:
        headers = {}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self._initialized:
            headers["MCP-Protocol-Version"] = PROTOCOL_VERSION
        resp = self._http.post(self.base_url, json=payload, headers=headers, timeout=self.timeout)
        if resp.status_code == 404 and self._session_id:
            raise _SessionExpired()
        if resp.status_code in (401, 403):
            raise NexusError("Nexus rejected the token (401/403). Generate a new one in Nexus → Nexus MCP.")
        resp.raise_for_status()
        sid = resp.headers.get("Mcp-Session-Id")
        if sid:
            self._session_id = sid
        if not expect_reply or resp.status_code == 202 or not resp.content:
            return None
        ctype = resp.headers.get("Content-Type", "")
        if "text/event-stream" in ctype:
            return self._from_sse(resp.text, payload.get("id"))
        return resp.json()

    @staticmethod
    def _from_sse(body: str, want_id: Any) -> dict[str, Any]:
        messages, buf = [], []
        for line in body.splitlines():
            if line.startswith("data:"):
                buf.append(line[5:].lstrip())
            elif not line.strip() and buf:
                messages.append("\n".join(buf))
                buf = []
        if buf:
            messages.append("\n".join(buf))
        for raw in messages:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == want_id and ("result" in msg or "error" in msg):
                return msg
        raise NexusError("No JSON-RPC reply found in the event stream")

    def _initialize(self) -> None:
        self._session_id = None
        self._initialized = False
        reply = self._post({
            "jsonrpc": "2.0", "id": next(self._ids), "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mesa-whatsapp-bot", "version": __version__},
            },
        })
        if reply and "error" in reply:
            raise NexusError(f"initialize failed: {reply['error']}")
        self._initialized = True
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_reply=False)

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        payload_args = {k: v for k, v in (arguments or {}).items() if v is not None}
        with self._lock:
            for attempt in range(3):
                try:
                    if not self._initialized:
                        self._initialize()
                    reply = self._post({
                        "jsonrpc": "2.0", "id": next(self._ids), "method": "tools/call",
                        "params": {"name": name, "arguments": payload_args},
                    })
                    break
                except _SessionExpired:
                    self._initialized = False
                except (requests.ConnectionError, requests.Timeout) as exc:
                    if attempt == 2:
                        raise NexusError(f"Nexus unreachable: {exc}") from exc
                    _time.sleep(2 ** attempt)
            else:
                raise NexusError("Nexus session could not be established")
        return _unwrap(name, reply)


def _unwrap(name: str, reply: dict[str, Any] | None) -> Any:
    if reply is None:
        raise NexusError(f"{name}: empty reply")
    if "error" in reply:
        raise NexusError(f"{name}: {reply['error'].get('message', reply['error'])}")
    result = reply.get("result", {})
    texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    if result.get("isError"):
        raise NexusError(f"{name}: {' '.join(texts) or 'tool error'}")
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    text = "\n".join(texts)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": text}


class FixtureClient:
    """Offline stand-in for NexusClient that reads <tool>.json files from a folder.

    Used by tests and by `python -m mesabot.cli preview ... --fixtures DIR`.
    """

    def __init__(self, directory: str, now: datetime | None = None):
        self.directory = directory
        self.now = now
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _read(self, name: str) -> Any:
        path = os.path.join(self.directory, f"{name}.json")
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        args = arguments or {}
        self.calls.append((name, args))
        data = self._read(name)
        now = self.now or now_ist()
        if name == "get_me" and self.now:
            data = {**data, "now": iso(self.now)}
        if name == "list_assignments":
            status = args.get("status", "due")
            items = data.get("assignments", [])
            if status != "all":
                items = [a for a in items if _assignment_status(a, now) == status]
            data = {"assignments": items}
        if name == "get_schedule":
            start = parse_iso(args.get("from")) or now
            end = parse_iso(args.get("to")) or start + timedelta(days=7)
            query = (args.get("query") or "").lower()
            events = [e for e in data.get("events", [])
                      if start <= parse_iso(e["start"]) < end
                      and (not query or query in f"{e.get('title','')} {e.get('course','')}".lower())]
            data = {**data, "events": events}
        if name == "list_announcements":
            since = now - timedelta(days=int(args.get("sinceDays", 7)))
            query = (args.get("query") or "").lower()
            data = {**data, "announcements": [
                a for a in data.get("announcements", [])
                if (parse_iso(a["postedAt"]) or now) >= since
                and (not query or query in f"{a.get('title','')} {a.get('body','')}".lower())]}
        if name == "list_notifications":
            since = now - timedelta(days=int(args.get("sinceDays", 30)))
            items = [n for n in data.get("notifications", [])
                     if (parse_iso(n["createdAt"]) or now) >= since]
            if args.get("unreadOnly"):
                items = [n for n in items if not n.get("read")]
            data = {**data, "notifications": items[: int(args.get("limit", 30))]}
        return data


def _assignment_status(a: dict[str, Any], now: datetime) -> str:
    if a.get("submissionStatus") == "submitted":
        return "submitted"
    due = parse_iso(a.get("dueAt"))
    return "overdue" if due and due < now else "due"


class Nexus:
    """Typed wrappers around the Nexus MCP tools, memoized for the lifetime of one job."""

    def __init__(self, client: NexusClient | FixtureClient):
        self.client = client
        self._memo: dict[str, Any] = {}

    def fresh(self) -> "Nexus":
        """A new view with an empty memo (one per scheduled job or inbound message)."""
        return Nexus(self.client)

    def _call(self, _tool: str, **args: Any) -> Any:
        key = f"{_tool}:{json.dumps(args, sort_keys=True, default=str)}"
        if key not in self._memo:
            self._memo[key] = self.client.call_tool(_tool, args)
        return self._memo[key]

    def get_me(self) -> dict[str, Any]:
        return self._call("get_me")

    def now(self) -> datetime:
        try:
            return parse_iso(self.get_me().get("now")) or now_ist()
        except NexusError:
            return now_ist()

    def list_courses(self) -> list[dict[str, Any]]:
        return self._call("list_courses").get("courses", [])

    def list_assignments(self, status: str = "due") -> list[dict[str, Any]]:
        return self._call("list_assignments", status=status).get("assignments", [])

    def list_announcements(self, since_days: int = 90, query: str | None = None) -> list[dict[str, Any]]:
        return self._call("list_announcements", sinceDays=since_days, query=query).get("announcements", [])

    def list_notifications(self, since_days: int = 30, limit: int = 100,
                           unread_only: bool = False) -> list[dict[str, Any]]:
        return self._call("list_notifications", sinceDays=since_days, limit=limit,
                          unreadOnly=unread_only).get("notifications", [])

    def get_schedule(self, start: datetime, end: datetime, query: str | None = None) -> list[dict[str, Any]]:
        """Sessions between start and end. Splits ranges longer than Nexus's 62-day limit."""
        events: list[dict[str, Any]] = []
        cursor = start
        while cursor < end:
            chunk_end = min(end, cursor + timedelta(days=62))
            events += self._call("get_schedule", **{"from": iso(cursor), "to": iso(chunk_end),
                                                    "query": query}).get("events", [])
            cursor = chunk_end
        return sorted(events, key=lambda e: e.get("start", ""))

    def get_grades(self) -> dict[str, Any]:
        return self._call("get_grades")

    def get_attendance_summary(self, course: str | None = None) -> dict[str, Any]:
        return self._call("get_attendance_summary", course=course)

    def get_assignment(self, assignment_id: str) -> dict[str, Any]:
        return self._call("get_assignment", assignmentId=assignment_id)

    def load_skill(self, name: str, args: dict[str, str] | None = None) -> dict[str, Any]:
        return self._call("load_skill", name=name, args=args)

    # ---- reminder-automation skill: the primary deadline feed ----------------------------

    def reminder_feed(self, hours: int = 24) -> list[dict[str, Any]]:
        """Run the Nexus `reminder-automation` skill and return its stable item shape.

        The skill describes the steps (get_me → get_schedule → list_assignments(due) →
        list_notifications(unread)) and the output contract:
        {kind: class|deadline|notification, at, title, where, link}. We load the skill
        so a change on the Nexus side is visible in the logs, then execute its steps.
        An extra `id` field carries the Nexus id so items can be de-duplicated.
        """
        hours = max(1, min(168, hours))
        try:
            skill = self.load_skill("reminder-automation", {"hours": str(hours)})
            expected = {"get_me", "get_schedule", "list_assignments", "list_notifications"}
            if set(skill.get("tools", [])) - expected:
                log.warning("reminder-automation now uses extra tools: %s", skill.get("tools"))
        except NexusError as exc:
            log.warning("reminder-automation skill unavailable (%s); running its steps directly", exc)

        now = self.now()
        end = now + timedelta(hours=hours)
        feed: list[dict[str, Any]] = []
        for event in self.get_schedule(now, end):
            if event.get("cancelled"):
                continue
            feed.append({
                "kind": "class", "at": event.get("start"), "title": event.get("title"),
                "where": event.get("room") or event.get("meetingLink") or "",
                "link": event.get("meetingLink") or "", "id": event.get("id"),
            })
        for a in self.list_assignments("due"):
            due = parse_iso(a.get("dueAt"))
            if due and now <= due <= end:
                feed.append({
                    "kind": "deadline", "at": iso(due), "title": a.get("title"),
                    "where": a.get("course") or "", "link": "", "id": a.get("id"),
                })
        try:
            for n in self.list_notifications(since_days=7, limit=50, unread_only=True):
                feed.append({
                    "kind": "notification", "at": iso(parse_iso(n.get("createdAt")) or now),
                    "title": n.get("title"), "where": "",
                    "link": (n.get("links") or {}).get("url", ""), "id": n.get("id"),
                })
        except NexusError as exc:
            log.info("notifications not available for the feed: %s", exc)
        return sorted(feed, key=lambda item: item.get("at") or "")


def build_client(base_url: str | None, token: str | None, fixtures: str | None = None,
                 now: datetime | None = None) -> NexusClient | FixtureClient:
    if fixtures:
        return FixtureClient(fixtures, now=now.astimezone(IST) if now else None)
    return NexusClient(base_url or "", token or "")

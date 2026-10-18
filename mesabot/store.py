"""The JSON log that persists on the Railway volume.

Schema (see CLAUDE.md for the full description):

{
  "version": 1,
  "meta": {...},
  "flagged_urls":  {key: {url, label, kind, source, source_id, source_title, deadline, first_seen, done, done_at}},
  "attachments":   {key: {filename, source, source_id, source_title, first_seen, read, read_at}},
  "done_items":    {key: {label, kind, done_at}},
  "alert_history": {key: {title, last_sent, count, history: [iso, ...]}},
  "mrs":           {week_start: {module, deadline, detected_from, done, done_at}},
  "grade_alerts":  {key: {assignment, course, marks, max_marks, released_at, alerted_at, channel}}
}
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Iterator

from .timeutil import iso, now_ist, parse_iso

SCHEMA_VERSION = 1
SECTIONS = ("meta", "flagged_urls", "attachments", "done_items", "alert_history", "mrs", "grade_alerts")
HISTORY_CAP = 20


def short_hash(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


def empty_log() -> dict[str, Any]:
    log: dict[str, Any] = {"version": SCHEMA_VERSION}
    for section in SECTIONS:
        log[section] = {}
    log["meta"]["created_at"] = iso(now_ist())
    return log


class Store:
    """Thread-safe JSON file store. Writes are atomic (temp file + rename)."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._data = self._load()

    @classmethod
    def in_dir(cls, data_dir: str) -> "Store":
        return cls(os.path.join(data_dir, "mesa_log.json"))

    def _load(self) -> dict[str, Any]:
        if not os.path.exists(self.path):
            return empty_log()
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            # Keep the broken file for inspection and start a fresh log.
            os.replace(self.path, self.path + ".corrupt")
            return empty_log()
        for section in SECTIONS:
            data.setdefault(section, {})
        data.setdefault("version", SCHEMA_VERSION)
        return data

    def _save(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".mesa_log.", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, self.path)

    @contextmanager
    def transaction(self) -> Iterator[dict[str, Any]]:
        with self._lock:
            yield self._data
            self._save()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    # ---- flagged URLs and attachments -------------------------------------------------

    def upsert_url(self, url: str, label: str, kind: str, source: str, source_id: str,
                   source_title: str, deadline: datetime | None) -> str:
        key = short_hash(url)
        with self.transaction() as data:
            entry = data["flagged_urls"].get(key)
            if entry is None:
                data["flagged_urls"][key] = {
                    "url": url, "label": label, "kind": kind, "source": source,
                    "source_id": source_id, "source_title": source_title,
                    "deadline": iso(deadline) if deadline else None,
                    "first_seen": iso(now_ist()), "done": False, "done_at": None,
                }
            else:
                # The same form is often re-posted with an extended deadline: keep the latest.
                if deadline and (not entry.get("deadline") or iso(deadline) > entry["deadline"]):
                    entry["deadline"] = iso(deadline)
                entry["source_title"] = source_title
        return key

    def upsert_attachment(self, filename: str, source: str, source_id: str, source_title: str) -> str:
        key = short_hash(filename.lower(), source_id)
        with self.transaction() as data:
            if key not in data["attachments"]:
                data["attachments"][key] = {
                    "filename": filename, "source": source, "source_id": source_id,
                    "source_title": source_title, "first_seen": iso(now_ist()),
                    "read": False, "read_at": None,
                }
        return key

    def url_done(self, key: str) -> bool:
        with self._lock:
            return bool(self._data["flagged_urls"].get(key, {}).get("done"))

    def attachment_read(self, key: str) -> bool:
        with self._lock:
            return bool(self._data["attachments"].get(key, {}).get("read"))

    def mark_url_done(self, key: str) -> None:
        with self.transaction() as data:
            if key in data["flagged_urls"]:
                data["flagged_urls"][key].update(done=True, done_at=iso(now_ist()))

    def mark_attachment_read(self, key: str) -> None:
        with self.transaction() as data:
            if key in data["attachments"]:
                data["attachments"][key].update(read=True, read_at=iso(now_ist()))

    # ---- generic done items (announcement actions, assignments, carry-forward) ---------

    def is_done(self, key: str) -> bool:
        with self._lock:
            return key in self._data["done_items"]

    def mark_done(self, key: str, label: str, kind: str) -> None:
        with self.transaction() as data:
            data["done_items"][key] = {"label": label, "kind": kind, "done_at": iso(now_ist())}

    # ---- alert history -----------------------------------------------------------------

    def last_alert(self, key: str) -> datetime | None:
        with self._lock:
            return parse_iso(self._data["alert_history"].get(key, {}).get("last_sent"))

    def alerted_within(self, key: str, hours: float, now: datetime) -> bool:
        last = self.last_alert(key)
        return last is not None and now - last < timedelta(hours=hours)

    def record_alert(self, key: str, title: str, now: datetime) -> None:
        with self.transaction() as data:
            entry = data["alert_history"].setdefault(key, {"title": title, "count": 0, "history": []})
            entry.update(title=title, last_sent=iso(now), count=entry.get("count", 0) + 1)
            entry["history"] = (entry.get("history", []) + [iso(now)])[-HISTORY_CAP:]

    def prune_alerts(self, older_than_days: int, now: datetime) -> int:
        cutoff = now - timedelta(days=older_than_days)
        with self.transaction() as data:
            stale = [k for k, v in data["alert_history"].items()
                     if (parse_iso(v.get("last_sent")) or now) < cutoff]
            for k in stale:
                del data["alert_history"][k]
        return len(stale)

    # ---- MRS ---------------------------------------------------------------------------

    def mrs_week(self, week_key: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._data["mrs"].get(week_key)
            return dict(entry) if entry else None

    def set_mrs_week(self, week_key: str, module: str | None, deadline: datetime,
                     detected_from: str | None) -> dict[str, Any]:
        with self.transaction() as data:
            entry = data["mrs"].setdefault(week_key, {"done": False, "done_at": None})
            if module:
                entry["module"] = module
                entry["detected_from"] = detected_from
            entry.setdefault("module", None)
            entry.setdefault("detected_from", None)
            entry["deadline"] = iso(deadline)
            return dict(entry)

    def mark_mrs_done(self, week_key: str, deadline: datetime) -> None:
        with self.transaction() as data:
            entry = data["mrs"].setdefault(
                week_key, {"module": None, "detected_from": None, "deadline": iso(deadline)})
            entry.update(done=True, done_at=iso(now_ist()))

    # ---- grades ------------------------------------------------------------------------

    def grade_alerted(self, key: str) -> bool:
        with self._lock:
            return key in self._data["grade_alerts"]

    def record_grade(self, key: str, grade: dict[str, Any], channel: str, now: datetime) -> None:
        with self.transaction() as data:
            data["grade_alerts"][key] = {
                "assignment": grade.get("assignment") or grade.get("title"),
                "course": grade.get("course"),
                "marks": grade.get("marks"),
                "max_marks": grade.get("maxMarks"),
                "released_at": grade.get("releasedAt"),
                "alerted_at": iso(now),
                "channel": channel,
            }

    # ---- meta --------------------------------------------------------------------------

    def meta(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data["meta"].get(key, default)

    def set_meta(self, key: str, value: Any) -> None:
        with self.transaction() as data:
            data["meta"][key] = value

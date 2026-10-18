"""Regex extraction from announcement and notification text.

Pulls out Google Form links, other URLs, deadline dates/times written in plain
English ("Tuesday, 20th October, 5 PM", "by today (Oct 15), 11:59 PM",
"due by 11:59 PM on Monday"), attachment filenames, action tags and the MRS
weekly module. Text is staff-written data: it is parsed, never executed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from .timeutil import IST, WEEKDAYS, next_weekday

# ---- URLs and files --------------------------------------------------------------------

_URL_TAIL = r"[^\s<>\"'\]\[(){}]+"
URL_RE = re.compile(r"https?://" + _URL_TAIL, re.I)
GOOGLE_FORM_RE = re.compile(
    r"https?://(?:docs\.google\.com/forms/" + _URL_TAIL + r"|forms\.gle/[A-Za-z0-9_-]+)", re.I)
ATTACHMENT_EXT = r"pdf|docx?|xlsx?|pptx?|csv|zip|png|jpe?g|txt|key|numbers|pages|ipynb"
ATTACHMENT_RE = re.compile(r"(?<![\w/.-])([\w][\w\-.()&]{0,100}\.(?:" + ATTACHMENT_EXT + r"))\b", re.I)


def _clean_url(url: str) -> str:
    return url.rstrip(".,;:!?…*_")


def extract_urls(text: str) -> list[str]:
    seen: list[str] = []
    for match in URL_RE.findall(text or ""):
        url = _clean_url(match)
        if url not in seen:
            seen.append(url)
    return seen


def is_google_form(url: str) -> bool:
    return bool(GOOGLE_FORM_RE.match(url))


def extract_attachments(text: str, listed: list[str] | None = None) -> list[str]:
    """Attachment filenames from the `attachments` field plus any named in the text."""
    names: list[str] = []
    for name in listed or []:
        if name and name not in names:
            names.append(name)
    stripped = URL_RE.sub(" ", text or "")
    for match in ATTACHMENT_RE.findall(stripped):
        if match not in names:
            names.append(match)
    return names


# ---- dates and deadlines ---------------------------------------------------------------

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4,
    "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10, "nov": 11,
    "november": 11, "dec": 12, "december": 12,
}
_MONTH = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_WEEKDAY = r"(?:mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?"
_ORD = r"(?:st|nd|rd|th)?"

DAY_MONTH_RE = re.compile(
    rf"\b(?:{_WEEKDAY},?\s+)?(\d{{1,2}}){_ORD}\s+(?:of\s+)?{_MONTH}\.?(?:,?\s+(\d{{4}}))?\b", re.I)
MONTH_DAY_RE = re.compile(
    rf"\b(?:{_WEEKDAY},?\s+)?{_MONTH}\.?\s+(\d{{1,2}}){_ORD}(?:,?\s+(\d{{4}}))?\b", re.I)
RELATIVE_RE = re.compile(r"\b(today|tonight|tomorrow)\b", re.I)
WEEKDAY_REF_RE = re.compile(
    r"\b(?:on|by|this|next|till|until|before)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.I)
TIME_RE = re.compile(
    r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(?:[–-]\s*\d{1,2}(?:[:.]\d{2})?\s*)?(a\.?m\.?|p\.?m\.?)(?![a-z])"
    r"|\b(noon|midnight)\b|\b(0\d|1[3-9]|2[0-3]):([0-5]\d)\b",
    re.I)
DEADLINE_CUE_RE = re.compile(
    r"\b(deadline|due|by|before|till|until|closes?|closing|extended|submit\w*|last date|latest|no later than|opt[\s-]?in)\b",
    re.I)
EVENT_CUE_RE = re.compile(r"(📅|\bdate\s*:|\bscheduled (?:for|on)\b|\bwe(?:'|’)re (?:kicking|hosting))", re.I)


@dataclass
class DateMention:
    when: datetime
    has_time: bool
    is_deadline: bool
    context: str
    start: int = 0


def _parse_time(match: re.Match[str]) -> time | None:
    hour_s, minute_s, ampm, word, h24, m24 = match.groups()
    if word:
        return time(12, 0) if word.lower() == "noon" else time(23, 59)
    if h24 is not None:
        return time(int(h24), int(m24))
    hour, minute = int(hour_s), int(minute_s or 0)
    if not 1 <= hour <= 12 or minute > 59:
        return None
    pm = ampm.lower().startswith("p")
    if pm and hour != 12:
        hour += 12
    if not pm and hour == 12:
        hour = 0
    return time(hour, minute)


def _resolve_year(day: int, month: int, year: int | None, posted: date) -> date | None:
    try:
        if year:
            return date(year, month, day)
        candidate = date(posted.year, month, day)
    except ValueError:
        return None
    # "5 Jan" written in December means next year.
    if candidate < posted - timedelta(days=60):
        candidate = date(posted.year + 1, month, day)
    return candidate


def _nearest_time(text: str, start: int, end: int) -> tuple[time | None, int]:
    """Closest time expression to a date mention, within the same sentence-ish window."""
    best: tuple[time | None, int] = (None, 10**9)
    window_start, window_end = max(0, start - 45), min(len(text), end + 60)
    for match in TIME_RE.finditer(text, window_start, window_end):
        parsed = _parse_time(match)
        if parsed is None:
            continue
        # Prefer a time after the date ("20th October, 5 PM") over one before.
        distance = match.start() - end if match.start() >= end else (start - match.end()) * 1.5
        if 0 <= distance < best[1]:
            best = (parsed, int(distance))
    return best


def _is_deadline(text: str, start: int, title: str) -> bool:
    # Look back to the start of the sentence (or line), at most 140 characters.
    boundary = max(text.rfind("\n", 0, start), text.rfind(". ", 0, start)) + 1
    before = text[max(boundary, start - 140):start]
    if DEADLINE_CUE_RE.search(before):
        return True
    if re.search(r"deadline|due|reminder|last date", title, re.I) and not EVENT_CUE_RE.search(before):
        return True
    return False


def extract_dates(text: str, posted: datetime, title: str = "") -> list[DateMention]:
    """Every date mentioned in the text, resolved against the posting date (IST)."""
    text = text or ""
    posted_day = posted.astimezone(IST).date()
    found: list[tuple[int, int, date, bool]] = []  # start, end, day, implied-late (tonight)

    for match in DAY_MONTH_RE.finditer(text):
        d = _resolve_year(int(match.group(1)), _month_key(match.group(2)),
                          int(match.group(3)) if match.group(3) else None, posted_day)
        if d:
            found.append((match.start(), match.end(), d, False))
    for match in MONTH_DAY_RE.finditer(text):
        if any(s <= match.start() < e for s, e, _, _ in found):
            continue
        d = _resolve_year(int(match.group(2)), _month_key(match.group(1)),
                          int(match.group(3)) if match.group(3) else None, posted_day)
        if d:
            found.append((match.start(), match.end(), d, False))
    for match in RELATIVE_RE.finditer(text):
        word = match.group(1).lower()
        d = posted_day + timedelta(days=1 if word == "tomorrow" else 0)
        found.append((match.start(), match.end(), d, word == "tonight"))
    for match in WEEKDAY_REF_RE.finditer(text):
        weekday = WEEKDAYS.index(match.group(1).lower())
        include_today = not match.group(0).lower().startswith("next")
        d = next_weekday(posted_day, weekday, include_today=include_today)
        found.append((match.start(1), match.end(), d, False))

    mentions: list[DateMention] = []
    for start, end, day, tonight in sorted(found):
        clock, _ = _nearest_time(text, start, end)
        deadline = _is_deadline(text, start, title) or tonight
        if clock is None:
            clock = time(23, 59) if (deadline or tonight) else time(9, 0)
            has_time = False
        else:
            has_time = True
        when = datetime.combine(day, clock, IST)
        context = text[max(0, start - 50): end + 40].replace("\n", " ").strip()
        # The same moment mentioned twice ("today (Oct 15), 11:59 PM") is one mention;
        # two different times on one day ("6 PM today" and "11:59 PM today") stay separate.
        current = next((m for m in mentions if m.when.date() == day and
                        (not m.has_time or not has_time or m.when == when)), None)
        if current is None:
            mentions.append(DateMention(when, has_time, deadline, context, start))
            continue
        if has_time and not current.has_time:
            current.when, current.has_time = when, True
        current.is_deadline = current.is_deadline or deadline
    return sorted(mentions, key=lambda m: m.when)


def _month_key(raw: str) -> int:
    key = raw.lower().rstrip(".")
    if key in MONTHS:
        return MONTHS[key]
    return MONTHS[key[:3]]


# ---- action tags -----------------------------------------------------------------------

ACTION_TAGS: dict[str, re.Pattern[str]] = {
    "opt-in": re.compile(r"\bopt[\s-]?in\b", re.I),
    "form": re.compile(r"\bgoogle form\b|\bfill (?:out|in)?\s*(?:the|this)?\s*form\b|\bform\b", re.I),
    "pre-read": re.compile(r"\bpre[\s-]?reads?\b", re.I),
    "pre-work": re.compile(r"\bpre[\s-]?work\b", re.I),
    "one-pager": re.compile(r"\bone[\s-]?pager\b", re.I),
    "case study": re.compile(r"\bcase stud(?:y|ies)\b|\bcase discussion\b", re.I),
    "submission": re.compile(r"\bsubmi(?:t|ssion)\b", re.I),
    "rsvp": re.compile(r"\brsvp\b", re.I),
    "mandatory": re.compile(r"\bmandatory\b|\bcompulsory\b|\bmust attend\b", re.I),
    "action required": re.compile(r"\baction required\b|\bimportant\b", re.I),
    "apply": re.compile(r"\bapply\b|\bapplications? close\b", re.I),
}
STRONG_TAGS = {"opt-in", "form", "pre-read", "pre-work", "one-pager", "case study",
               "submission", "rsvp", "mandatory", "action required"}
# Announcements with these tags get a 48h-before-the-session reminder.
PREP_TAGS = {"pre-work", "one-pager", "case study", "opt-in", "pre-read"}


def action_tags(title: str, body: str) -> list[str]:
    text = f"{title}\n{body}"
    return [tag for tag, pattern in ACTION_TAGS.items() if pattern.search(text)]


# ---- MRS Practice Lab ------------------------------------------------------------------

MRS_RE = re.compile(r"\bMRS\b|practice lab", re.I)
MRS_MODULE_RES = [
    re.compile(r"assigned module is\s+\**([A-Z][^.\n*]{2,60}?)\**\s*(?:[.\n]|$)", re.I),
    re.compile(r"complete the\s+([A-Z][\w' -]{2,60}?)\s+session on MRS", re.I),
    re.compile(r"No\s+([A-Z][\w' -]{2,60}?)\s+session yet this week", re.I),
    re.compile(r"this week'?s (?:MRS )?module(?: is)?:?\s+\**([A-Z][^.\n*]{2,60}?)\**\s*(?:[.\n]|$)", re.I),
]


def mrs_module(text: str) -> str | None:
    for pattern in MRS_MODULE_RES:
        match = pattern.search(text or "")
        if match:
            return match.group(1).strip(" *_\"'")
    return None


# ---- one parsed source -----------------------------------------------------------------

@dataclass
class ParsedText:
    source: str              # "announcement" | "notification"
    source_id: str
    title: str
    body: str
    posted: datetime
    course: str | None
    urls: list[str] = field(default_factory=list)
    forms: list[str] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)
    dates: list[DateMention] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    pinned: bool = False

    @property
    def deadlines(self) -> list[DateMention]:
        return [d for d in self.dates if d.is_deadline]

    @property
    def event_dates(self) -> list[DateMention]:
        return [d for d in self.dates if not d.is_deadline]

    @property
    def actionable(self) -> bool:
        return bool(set(self.tags) & STRONG_TAGS) or bool(self.forms)

    @property
    def is_prep(self) -> bool:
        return bool(set(self.tags) & PREP_TAGS)


def parse_text(source: str, source_id: str, title: str, body: str, posted: datetime,
               course: str | None = None, listed_attachments: list[str] | None = None,
               pinned: bool = False) -> ParsedText:
    urls = extract_urls(body)
    return ParsedText(
        source=source, source_id=source_id, title=title or "", body=body or "", posted=posted,
        course=course, urls=urls, forms=[u for u in urls if is_google_form(u)],
        attachments=extract_attachments(body, listed_attachments),
        dates=extract_dates(body, posted, title or ""), tags=action_tags(title or "", body or ""),
        pinned=pinned,
    )

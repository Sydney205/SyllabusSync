"""Extract class events from free-text announcements.

Tuned for announcements like:

    📢Live Lesson Reminder📢
    COS 102 - Problem Solving
    🗓Date: Tuesday 20th October, 2026
    🕓 Time: 3:00PM - 4:00PM (WAT)
    🔗 Meeting link: https://meet.google.com/oxo-dctc-hsw

It also handles:
  * several reminders pasted/forwarded in one message,
  * several courses under one reminder (later courses inherit the last date),
  * a looser fallback such as "Live class: Data Structures tomorrow 3pm - 4:30pm".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from dateutil import parser as dateparser

import config

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

URL_RE = re.compile(r"https?://[^\s<>()]+", re.I)

# "COS 102 - Problem Solving", "MIVA-CSC 106 - Introduction to ...", "GST 112"
COURSE_RE = re.compile(
    r"^(?P<code>(?:[A-Z]{2,}-)?[A-Z]{2,4}\s?\d{3}[A-Z]?)\b"
    r"(?:\s*[-–—:]\s*(?P<name>.+))?$"
)
HEADER_RE = re.compile(r"\b(reminder|announcement|schedule update|notice)\b", re.I)
DATE_LABEL_RE = re.compile(r"^date\s*[:\-]?\s*(?P<v>.+)$", re.I)
TIME_LABEL_RE = re.compile(r"^time\s*[:\-]?\s*(?P<v>.+)$", re.I)
NOTE_LABEL_RE = re.compile(r"^notes?\s*[:\-]\s*(?P<v>.+)$", re.I)
GREETING_RE = re.compile(r"^(hi|hello|hey|dear|good (morning|afternoon|evening)|see you|don'?t miss)\b", re.I)
KEYWORD_RE = re.compile(
    r"\b(class(es)?|lessons?|lectures?|tutorials?|seminars?|exams?|tests?|quiz(zes)?|"
    r"sessions?|revision|webinars?|live)\b",
    re.I,
)

_T = r"(?P<h{n}>\d{{1,2}})(?:[:.](?P<m{n}>\d{{2}}))?\s*(?P<ap{n}>[ap]\.?\s?m\.?)?"
TIME_RANGE_RE = re.compile(
    r"(?<![\d/:.-])" + _T.format(n=1) + r"\s*(?:-|–|—|to|till|until)\s*" + _T.format(n=2), re.I
)
SINGLE_TIME_RE = re.compile(
    r"\b(?P<h1>\d{1,2})(?:(?::|\.)(?P<m1>\d{2}))?\s*(?P<ap1>[ap]\.?\s?m\.?)(?![a-z])"
    r"|\b(?P<h2>[01]?\d|2[0-3]):(?P<m2>\d{2})\b",
    re.I,
)
DURATION_RE = re.compile(
    r"(?:(?P<hours>\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\b)?\s*(?:and\s*)?"
    r"(?:(?P<mins>\d+)\s*(?:minutes?|mins?)\b)?",
    re.I,
)

MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEKDAY_RE = re.compile(r"\b(" + "|".join(WEEKDAYS) + r"|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b,?", re.I)
ORDINAL_RE = re.compile(r"\b(\d{1,2})(st|nd|rd|th)\b", re.I)
DATE_IN_TEXT_RE = re.compile(
    rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{MONTHS},?(?:\s+\d{{4}})?"
    rf"|\b{MONTHS}\s+\d{{1,2}}(?:st|nd|rd|th)?,?(?:\s+\d{{4}})?"
    r"|\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}/\d{1,2}/\d{2,4}\b"
    r"|\b(?:today|tomorrow|tonight)\b"
    r"|\b(?:next\s+|this\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.I,
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ParsedEvent:
    title: str
    start: datetime
    end: datetime
    link: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def description(self) -> str:
        parts = []
        if self.link:
            parts.append(f"Meeting link: {self.link}")
        parts.extend(self.notes)
        parts.append("Added by SyllabusSync.")
        return "\n\n".join(parts)


@dataclass
class ParseResult:
    events: list[ParsedEvent] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # clarification requests
    relevant: bool = False  # False => looks like ordinary chatter; ignore silently


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean_line(line: str) -> str:
    """Strip emojis/bullets/dashes around a line, keeping the text."""
    line = line.strip()
    line = re.sub(r"^[^\w(]+", "", line)
    line = re.sub(r"[^\w).!?/]+$", "", line)
    return line.strip()


def _now() -> datetime:
    return datetime.now(config.TZ)


def _to_24h(hour: int, minute: int, ampm: str | None) -> tuple[int, int]:
    if ampm:
        is_pm = ampm.lower().startswith("p")
        if is_pm and hour < 12:
            hour += 12
        elif not is_pm and hour == 12:
            hour = 0
    return hour, minute


def parse_time_range(text: str) -> tuple[time, time | None] | None:
    """Return (start, end) or (start, None) if only a start time is present."""
    m = TIME_RANGE_RE.search(text)
    if m:
        h1, m1, ap1 = int(m["h1"]), int(m["m1"] or 0), m["ap1"]
        h2, m2, ap2 = int(m["h2"]), int(m["m2"] or 0), m["ap2"]
        # Only accept bare-number ranges ("3 - 4") if they look like clock times.
        if ap1 or ap2 or m["m1"] or m["m2"]:
            if h1 <= 23 and h2 <= 23 and m1 < 60 and m2 < 60:
                if not ap1 and ap2:
                    # "3 - 4pm" -> both PM; "11 - 1pm" -> 11AM - 1PM
                    sh, sm = _to_24h(h1, m1, ap2)
                    eh, em = _to_24h(h2, m2, ap2)
                    if (sh, sm) >= (eh, em):
                        sh, sm = _to_24h(h1, m1, "am")
                else:
                    sh, sm = _to_24h(h1, m1, ap1)
                    eh, em = _to_24h(h2, m2, ap2 or ap1)
                if not ap1 and not ap2 and 1 <= h1 <= 6:
                    sh, eh = sh + 12, (eh + 12 if eh < 12 else eh)  # "3:00 - 4:00" -> afternoon
                return time(sh, sm), time(eh % 24, em)

    m = SINGLE_TIME_RE.search(text)
    if m:
        if m["h1"]:
            h, mi = _to_24h(int(m["h1"]), int(m["m1"] or 0), m["ap1"])
        else:
            h, mi = int(m["h2"]), int(m["m2"])
        if h <= 23 and mi < 60:
            return time(h, mi), None
    return None


def parse_duration(text: str) -> timedelta | None:
    for m in DURATION_RE.finditer(text):
        if m["hours"] or m["mins"]:
            hours = float(m["hours"] or 0)
            mins = int(m["mins"] or 0)
            total = timedelta(hours=hours, minutes=mins)
            if total > timedelta(0):
                return total
    return None


def parse_date(text: str, now: datetime | None = None) -> date | None:
    now = now or _now()
    today = now.date()
    low = text.lower()

    if "today" in low or "tonight" in low:
        return today
    if "tomorrow" in low:
        return today + timedelta(days=1)

    cleaned = ORDINAL_RE.sub(r"\1", text)
    cleaned = re.sub(r"\(.*?\)", " ", cleaned)
    without_weekday = WEEKDAY_RE.sub(" ", cleaned).replace(",", " ").strip()

    # Only a weekday ("Monday", "next Friday") -> next occurrence.
    if not re.search(r"\d", without_weekday) and not re.search(MONTHS, without_weekday, re.I):
        wd = WEEKDAY_RE.search(cleaned)
        if not wd:
            return None
        name = wd.group(1).lower()
        idx = next(i for i, d in enumerate(WEEKDAYS) if d.startswith(name[:3]))
        delta = (idx - today.weekday()) % 7
        if "next" in low and delta == 0:
            delta = 7
        return today + timedelta(days=delta)

    try:
        default = datetime(today.year, today.month, today.day)
        parsed = dateparser.parse(without_weekday, fuzzy=True, dayfirst=True, default=default).date()
    except (ValueError, OverflowError):
        return None

    has_year = bool(re.search(r"\b\d{4}\b", without_weekday) or re.search(r"/\d{2}\b", without_weekday))
    if not has_year and parsed < today - timedelta(days=30):
        parsed = parsed.replace(year=parsed.year + 1)
    return parsed


def _combine(d: date, start: time, end: time | None, duration: timedelta | None) -> tuple[datetime, datetime]:
    start_dt = datetime.combine(d, start, tzinfo=config.TZ)
    if end is not None:
        end_dt = datetime.combine(d, end, tzinfo=config.TZ)
        if end_dt <= start_dt:  # crosses midnight
            end_dt += timedelta(days=1)
    else:
        end_dt = start_dt + (duration or timedelta(minutes=config.DEFAULT_DURATION_MINUTES))
    return start_dt, end_dt


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def parse_announcement(text: str, now: datetime | None = None) -> ParseResult:
    now = now or _now()
    if not text or not text.strip():
        return ParseResult()

    structured = _parse_structured(text, now)
    if structured is not None:
        return structured
    return _parse_freeform(text, now)


def _parse_structured(text: str, now: datetime) -> ParseResult | None:
    """Parse announcements with a course-code line and Date:/Time: labels."""
    lines = [_clean_line(l) for l in text.splitlines()]
    lines = [l for l in lines if l]

    has_course = any(COURSE_RE.match(l) for l in lines)
    has_labels = any(DATE_LABEL_RE.match(l) or TIME_LABEL_RE.match(l) for l in lines)
    if not has_course:
        return None
    if not has_labels and not KEYWORD_RE.search(text):
        return ParseResult(relevant=False)  # e.g. "COS 102 - anyone has notes?"

    result = ParseResult(relevant=True)
    drafts: list[dict] = []
    current: dict | None = None
    carried_date: date | None = None
    revision = False
    block_notes: list[str] = []
    block_start = 0  # index into drafts where the current reminder block starts

    def finish_block():
        for d in drafts[block_start:]:
            d["notes"].extend(n for n in block_notes if n not in d["notes"])

    for line in lines:
        if HEADER_RE.search(line) and not COURSE_RE.match(line):
            # A new reminder begins: reset per-reminder state.
            finish_block()
            block_start = len(drafts)
            block_notes = []
            current = None
            carried_date = None
            revision = "revision" in line.lower()
            continue

        course = COURSE_RE.match(line)
        if course:
            code = re.sub(r"\s+", " ", course["code"]).strip()
            name = re.sub(r"\s+", " ", (course["name"] or "")).strip()
            title = f"{code} - {name}" if name else code
            if revision:
                title += " (Revision)"
            # Courses listed without their own Date: line inherit the latest date in the reminder.
            current = {"title": title, "date": None, "time": None, "link": "", "notes": [], "carried": carried_date}
            drafts.append(current)
            continue

        if m := DATE_LABEL_RE.match(line):
            d = parse_date(m["v"], now)
            carried_date = d or carried_date
            if current is not None:
                current["date"] = d
                current["date_raw"] = m["v"]
            continue

        if m := TIME_LABEL_RE.match(line):
            if current is not None:
                current["time"] = parse_time_range(m["v"])
                current["time_raw"] = m["v"]
                current["duration"] = parse_duration(m["v"])
            continue

        if url := URL_RE.search(line):
            if current is not None and not current["link"]:
                current["link"] = url.group(0).rstrip(".,")
            continue

        if m := NOTE_LABEL_RE.match(line):
            if current is not None:
                current["notes"].append(m["v"])
            continue

        if "revision" in line.lower():
            revision = True
            continue

        if line.lower().startswith("please"):
            block_notes.append(line)

    finish_block()

    for d in drafts:
        event_date = d["date"] or d["carried"]
        missing = []
        if event_date is None:
            missing.append("date" if "date_raw" not in d else f"date (couldn't read “{d['date_raw']}”)")
        if not d["time"]:
            missing.append("time" if "time_raw" not in d else f"time (couldn't read “{d['time_raw']}”)")
        if missing:
            result.problems.append(f"{d['title']}: missing {' and '.join(missing)}")
            continue
        start_t, end_t = d["time"]
        start_dt, end_dt = _combine(event_date, start_t, end_t, d.get("duration"))
        result.events.append(ParsedEvent(d["title"], start_dt, end_dt, d["link"], d["notes"]))
    return result


def _parse_freeform(text: str, now: datetime) -> ParseResult:
    """Fallback for loose one-off announcements without course-code lines."""
    if not KEYWORD_RE.search(text):
        return ParseResult(relevant=False)

    lines = [_clean_line(l) for l in text.splitlines()]
    lines = [l for l in lines if l]

    date_text = next((m["v"] for l in lines if (m := DATE_LABEL_RE.match(l))), None)
    time_text = next((m["v"] for l in lines if (m := TIME_LABEL_RE.match(l))), None)

    d = parse_date(date_text, now) if date_text else None
    if d is None:
        dm = DATE_IN_TEXT_RE.search(text)
        d = parse_date(dm.group(0), now) if dm else None
    t = parse_time_range(time_text or text)

    # Without either a date or a time this is probably just chatter about classes.
    if d is None and t is None:
        return ParseResult(relevant=False)

    title = ""
    for l in lines:
        if (
            DATE_LABEL_RE.match(l)
            or TIME_LABEL_RE.match(l)
            or URL_RE.fullmatch(l)
            or GREETING_RE.match(l)
            or (HEADER_RE.search(l) and len(lines) > 1)
            or l.endswith(":") and len(l.split()) <= 3  # "Miva Admin:"
        ):
            continue
        stripped = URL_RE.sub(" ", l)
        stripped = TIME_RANGE_RE.sub(" ", stripped)
        stripped = SINGLE_TIME_RE.sub(" ", stripped)
        stripped = DATE_IN_TEXT_RE.sub(" ", stripped)
        stripped = re.sub(r"\b(at|on|from|by|for)\s*$", " ", stripped.strip(), flags=re.I)
        stripped = re.sub(r"\s+", " ", stripped).strip(" -–,:;.")
        title = (stripped or re.sub(r"\s+", " ", l))[:120]
        break
    title = title or "Class"

    result = ParseResult(relevant=True)
    missing = [name for name, v in (("date", d), ("time", t)) if v is None]
    if missing:
        result.problems.append(f"{title}: missing {' and '.join(missing)}")
        return result

    link_m = URL_RE.search(text)
    start_dt, end_dt = _combine(d, t[0], t[1], parse_duration(text))
    result.events.append(
        ParsedEvent(title, start_dt, end_dt, link_m.group(0).rstrip(".,") if link_m else "")
    )
    return result

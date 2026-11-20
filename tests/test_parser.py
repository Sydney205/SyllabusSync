import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from announcement_parser import parse_announcement, parse_time_range  # noqa: E402

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=config.TZ)

SAMPLE = """Miva Admin:
📢Live Lesson Reminder📢

Hi Miva Stars!

Don't miss today's live lessons for:

COS 102 - Problem Solving
🗓Date: Tuesday 20th October, 2026
🕓 Time: 3:00PM - 4:00PM (WAT)
🔗 Meeting link: https://meet.google.com/oxo-dctc-hsw

Please ensure that you join the class using your school email

See you there! 📚

📢Live Lesson Reminder📢

Hi Miva Stars!

Don't miss today's live lessons for:

MIVA-CSC 106 - Introduction to Web Technologies
🗓Date: Wednesday 21st October, 2026
🕓 Time: 4:00PM - 5:00PM (WAT)
🔗 Meeting link: https://meet.google.com/zac-wbxi-byo

Please ensure that you join the class using your school email

See you there! 📚

Miva Admin:
📢Revision Live Lesson Reminder📢

Hi Miva Stars! 🌟

Don't miss today's revision live lessons for:

COS 102 - Problem Solving
🗓Date: Tuesday, 17th November, 2026
🕓 Time: 3:00PM - 4:30PM (WAT)
🔗 Meeting link: https://meet.google.com/oxo-dctc-hsw

Please ensure that you join the class using your school email

See you there! 📚

-📢Revision Live Lesson Reminder📢

Hi Miva Stars! 🌟

Don't miss today's revision live lessons for:

GST 122 - Communication in English II
🗓Date: Wednesday, 18th November, 2026
🕓 Time: 1:30PM - 3:00PM (WAT)
🔗 Meeting link: http://meet.google.com/kpi-whfs-cvd

GST 112 - NG Peoples and Culture
🕓 Time: 4:30PM - 6:00PM (WAT)
🔗 Meeting link: http://meet.google.com/pkc-fhad-gir

Please ensure that you join the class using your school email

See you there! 📚
"""


def fmt(ev):
    return (ev.title, ev.start.strftime("%Y-%m-%d %H:%M"), ev.end.strftime("%H:%M"), ev.link)


def test_full_sample():
    r = parse_announcement(SAMPLE, NOW)
    assert r.relevant and not r.problems
    assert [fmt(e) for e in r.events] == [
        ("COS 102 - Problem Solving", "2026-10-20 15:00", "16:00", "https://meet.google.com/oxo-dctc-hsw"),
        ("MIVA-CSC 106 - Introduction to Web Technologies", "2026-10-21 16:00", "17:00",
         "https://meet.google.com/zac-wbxi-byo"),
        ("COS 102 - Problem Solving (Revision)", "2026-11-17 15:00", "16:30", "https://meet.google.com/oxo-dctc-hsw"),
        ("GST 122 - Communication in English II (Revision)", "2026-11-18 13:30", "15:00",
         "http://meet.google.com/kpi-whfs-cvd"),
        ("GST 112 - NG Peoples and Culture (Revision)", "2026-11-18 16:30", "18:00",
         "http://meet.google.com/pkc-fhad-gir"),
    ]
    assert "Please ensure that you join the class using your school email" in r.events[0].notes
    assert r.events[0].start.utcoffset().total_seconds() == 3600  # WAT = UTC+1


def test_single_without_blank_line():
    text = """📢Live Lesson Reminder📢
Don't miss today's live lesson for:
PHY 102 - General Physics II
🗓Date: Tuesday 27th October, 2026
🕓 Time: 4:00PM - 5:00PM (WAT)
🔗 Meeting link: https://meet.google.com/hzn-uiie-tfc"""
    r = parse_announcement(text, NOW)
    assert [fmt(e) for e in r.events] == [
        ("PHY 102 - General Physics II", "2026-10-27 16:00", "17:00", "https://meet.google.com/hzn-uiie-tfc")
    ]


def test_missing_date_asks_for_clarification():
    text = """📢Live Lesson Reminder📢
MTH 102 - Elementary Mathematics II
🕓 Time: 4:00PM - 5:00PM (WAT)"""
    r = parse_announcement(text, NOW)
    assert r.relevant and not r.events
    assert r.problems == ["MTH 102 - Elementary Mathematics II: missing date"]


def test_chatter_is_ignored():
    for text in ["good morning everyone", "COS 102 - anyone has the notes?", "lol ok", "see you at 3pm"]:
        assert not parse_announcement(text, NOW).relevant, text


def test_freeform():
    r = parse_announcement("Live class: Data Structures tomorrow 3pm - 4:30pm https://meet.google.com/x", NOW)
    assert [fmt(e) for e in r.events] == [
        ("Live class: Data Structures", "2026-11-21 15:00", "16:30", "https://meet.google.com/x")
    ]


def test_freeform_missing_time():
    r = parse_announcement("Revision class for CSC 201 is on Friday", NOW)
    assert r.relevant and not r.events and "missing time" in r.problems[0]


def test_time_ranges():
    def tr(s):
        a, b = parse_time_range(s)
        return a.strftime("%H:%M"), b.strftime("%H:%M") if b else None

    assert tr("3:00PM - 4:30PM (WAT)") == ("15:00", "16:30")
    assert tr("11 - 1pm") == ("11:00", "13:00")
    assert tr("3 - 4pm") == ("15:00", "16:00")
    assert tr("15:00 to 16:30") == ("15:00", "16:30")
    assert tr("at 10am") == ("10:00", None)

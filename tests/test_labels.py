import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from announcement_parser import parse_announcement  # noqa: E402
from labels import LABELS, classify_text, label_from_event, normalize_label  # noqa: E402

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=config.TZ)


def test_label_colours_match_google_calendar():
    assert {n: (l.color, l.color_id) for n, l in LABELS.items()} == {
        "Exam": ("#DC2626", "11"),
        "Meeting": ("#D97706", "5"),
        "Event": ("#059669", "10"),
        "Live class": ("#2563EB", "9"),
    }


def test_normalize_and_infer_label():
    assert normalize_label("live CLASS") == "Live class"
    assert normalize_label("Party") is None
    assert label_from_event("Exam", "9") == "Exam"  # stored label wins over colour
    assert label_from_event(None, "5") == "Meeting"
    assert label_from_event(None, None) == "Live class"  # events from before labels existed


def test_classify_text():
    cases = {
        "📢Live Lesson Reminder📢 COS 102 - Problem Solving": "Live class",
        "Revision live lesson ahead of your exams": "Live class",
        "📢Exam Reminder📢 COS 102 - Problem Solving": "Exam",
        "CBT test for GST 112 on Friday": "Exam",
        "Class reps meeting tomorrow 4pm": "Meeting",
        "Town hall with the dean": "Meeting",
        "Matriculation ceremony on Saturday": "Event",
        "COS 102 - Problem Solving": "Live class",
        "Football on Saturday": "Event",
    }
    for text, expected in cases.items():
        assert classify_text(text) == expected, text


def test_parsed_events_carry_block_context():
    text = """📢Exam Reminder📢
Don't miss your exam for:
COS 102 - Problem Solving
🗓Date: Tuesday 1st December, 2026
🕓 Time: 9:00AM - 11:00AM (WAT)

📢Live Lesson Reminder📢
PHY 102 - General Physics II
🗓Date: Wednesday 2nd December, 2026
🕓 Time: 4:00PM - 5:00PM (WAT)"""
    r = parse_announcement(text, NOW)
    assert [(e.title, classify_text(e.context)) for e in r.events] == [
        ("COS 102 - Problem Solving", "Exam"),
        ("PHY 102 - General Physics II", "Live class"),
    ]


def test_meeting_announcement_is_relevant():
    r = parse_announcement("Class reps meeting tomorrow 4pm - 5pm", NOW)
    assert r.relevant and len(r.events) == 1
    assert classify_text(r.events[0].context) == "Meeting"

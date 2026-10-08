"""Festive seasons: extra categories that switch themselves on about six weeks before each festival and off after it.
Each festival brings the things Singapore families buy for it; they are searched, saved and shown like the staples.

ponytail: dates are typed in for 2026-2028 (Chinese New Year, Hari Raya, Deepavali and Mid-Autumn move every year;
Hari Raya and 2027-28 Deepavali are the expected dates). Each January, check them against MOM's public-holiday list and add the next year.
"""
from __future__ import annotations

from datetime import date, timedelta

LEAD = timedelta(days=42)   # festive shopping starts about six weeks ahead
AFTER = timedelta(days=1)   # gone the day after
ITEMS = {   # festival -> (emoji, Chinese name, what families buy)
    "Deepavali": ("🪔", "屠妖节", ["ghee", "murukku", "basmati rice", "jaggery"]),
    "Christmas": ("🎄", "圣诞节", ["turkey", "log cake", "ham", "panettone"]),
    "Chinese New Year": ("🧧", "农历新年", ["mandarin orange", "bak kwa", "pineapple tart", "abalone", "waxed sausage"]),
    "Hari Raya Puasa": ("🌙", "开斋节", ["dates", "ketupat", "rendang paste", "kuih"]),
    "Hari Raya Haji": ("🐑", "哈芝节", ["mutton", "rendang paste", "basmati rice"]),
    "Mid-Autumn": ("🥮", "中秋节", ["mooncake", "pomelo", "chinese tea"]),
}
DATES = [
    ("Deepavali", date(2026, 11, 8)), ("Christmas", date(2026, 12, 25)),
    ("Chinese New Year", date(2027, 2, 6)), ("Hari Raya Puasa", date(2027, 3, 10)), ("Hari Raya Haji", date(2027, 5, 17)),
    ("Mid-Autumn", date(2027, 9, 15)), ("Deepavali", date(2027, 10, 28)), ("Christmas", date(2027, 12, 25)),
    ("Chinese New Year", date(2028, 1, 26)), ("Hari Raya Puasa", date(2028, 2, 27)), ("Hari Raya Haji", date(2028, 5, 5)),
    ("Mid-Autumn", date(2028, 10, 3)), ("Deepavali", date(2028, 10, 17)), ("Christmas", date(2028, 12, 25)),
]


def active(today: date) -> list[dict]:
    """Festivals whose shopping window covers `today`: [{name, day, emoji, zh, items}], soonest first."""
    out = [dict(name=n, day=d, emoji=ITEMS[n][0], zh=ITEMS[n][1], items=ITEMS[n][2])
           for n, d in DATES if d - LEAD <= today <= d + AFTER]
    return sorted(out, key=lambda f: f["day"])


def terms(today: date) -> list[str]:
    """The extra search terms for today, without duplicates (rendang paste is both Hari Rayas)."""
    return list(dict.fromkeys(t for f in active(today) for t in f["items"]))

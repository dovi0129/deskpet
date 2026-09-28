"""Date facts for special-day lines. Lunar holidays are not computed (no table is
bundled); only fixed solar dates, weekdays, and the configured birthday."""
from __future__ import annotations

import re
import time
from typing import Optional

_BIRTHDAY_RE = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")

FIXED = {"01-01": "DATE_NEWYEAR", "12-24": "DATE_CHRISTMAS", "12-25": "DATE_CHRISTMAS",
         "12-31": "DATE_YEAREND"}


def valid_birthday(value: object) -> str:
    """Accept 'MM-DD' (also 'YYYY-MM-DD' / 'YYYY.MM.DD', year dropped). Else ''."""
    if not isinstance(value, str):
        return ""
    v = value.strip().replace(".", "-").replace("/", "-")
    parts = v.split("-")
    if len(parts) == 3 and len(parts[0]) == 4:
        v = f"{parts[1]:0>2}-{parts[2]:0>2}"
    elif len(parts) == 2:
        v = f"{parts[0]:0>2}-{parts[1]:0>2}"
    return v if _BIRTHDAY_RE.match(v) else ""


def date_facts(tm: Optional[time.struct_time] = None, birthday: str = "") -> set[str]:
    tm = tm or time.localtime()
    md = f"{tm.tm_mon:02d}-{tm.tm_mday:02d}"
    facts = set()
    if birthday and md == birthday:
        facts.add("DATE_BIRTHDAY")
    if md in FIXED:
        facts.add(FIXED[md])
    if tm.tm_wday == 4 and tm.tm_hour >= 17:
        facts.add("DATE_FRIDAY_EVENING")
    if tm.tm_wday == 0 and tm.tm_hour < 12:
        facts.add("DATE_MONDAY_MORNING")
    if tm.tm_wday >= 5:
        facts.add("DATE_WEEKEND")
    return facts

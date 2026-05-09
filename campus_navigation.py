"""
Class navigation & travel-time estimation for the Smart Campus Portal.

Room data loads from data/campus_rooms.json (room code, title, block, floor).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

_DATA_PATH = Path(__file__).resolve().parent / "data" / "campus_rooms.json"

# Walking model (seconds)
_SAME_CORRIDOR_FLOOR_SAME_BLOCK = 30
_STAIR_SECONDS_PER_FLOOR = 60
_INTER_BLOCK_SECONDS = 150  # middle of 2–3 minute range

_WEEKDAY_ALIASES = {
    "mon": "Monday",
    "monday": "Monday",
    "tue": "Tuesday",
    "tues": "Tuesday",
    "tuesday": "Tuesday",
    "wed": "Wednesday",
    "wednesday": "Wednesday",
    "thu": "Thursday",
    "thur": "Thursday",
    "thurs": "Thursday",
    "thursday": "Thursday",
    "fri": "Friday",
    "friday": "Friday",
    "sat": "Saturday",
    "saturday": "Saturday",
    "sun": "Sunday",
    "sunday": "Sunday",
}


def _ordinal_floor(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def load_campus_rooms(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or _DATA_PATH
    with open(p, encoding="utf-8") as fh:
        data = json.load(fh)
    return list(data["rooms"])


def _normalize_code_key(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


_room_by_code: dict[str, dict[str, Any]] | None = None
_all_rooms: list[dict[str, Any]] | None = None


def _ensure_index() -> None:
    global _room_by_code, _all_rooms
    if _room_by_code is not None:
        return
    _all_rooms = load_campus_rooms()
    _room_by_code = {}
    for r in _all_rooms:
        key = _normalize_code_key(r["code"])
        _room_by_code[key] = r


def reload_room_cache() -> None:
    """Call after edits to campus JSON (tests / hot reload)."""
    global _room_by_code, _all_rooms
    _room_by_code = None
    _all_rooms = None
    _ensure_index()


def resolve_room(schedule_room_field: str | None) -> dict[str, Any] | None:
    """Match a timetable room cell to structured campus metadata."""
    if not schedule_room_field:
        return None
    _ensure_index()
    assert _room_by_code is not None and _all_rooms is not None

    raw = schedule_room_field.strip()
    uc = raw.upper()

    tokens = re.findall(r"[A-Za-z]{2,}[\-.]?\d{1,4}|[A-Za-z]{2,}-\d{2}", raw)
    for tok in tokens:
        hit = _room_by_code.get(_normalize_code_key(tok))
        if hit:
            return hit

    for key in _room_by_code:
        code = _room_by_code[key]["code"]
        if code.upper() in uc or uc in code.upper():
            return _room_by_code[key]

    low = raw.lower()
    for r in _all_rooms:
        if r["title"].lower() in low or low in r["title"].lower():
            return r
    return None


def estimate_travel_seconds(from_room_field: str | None, to_room_field: str | None) -> int | None:
    """
    Estimated walking time including stairs and cross-block commute.

    Rules:
      - Same block, same floor: 30 seconds
      - Same block, different floors: 30 seconds + 1 minute per floor crossed
      - Different blocks: 2.5 minutes base + corridor/stair adjustment for floor delta
    """
    a = resolve_room(from_room_field)
    b = resolve_room(to_room_field)
    if not a or not b:
        return None
    if _normalize_code_key(a["code"]) == _normalize_code_key(b["code"]):
        return 0

    fa, fb = int(a["floor"]), int(b["floor"])
    block_a, block_b = a["block"], b["block"]

    floor_delta = abs(fa - fb)
    stair_segment = floor_delta * _STAIR_SECONDS_PER_FLOOR

    if block_a == block_b:
        if fa == fb:
            return _SAME_CORRIDOR_FLOOR_SAME_BLOCK
        return _SAME_CORRIDOR_FLOOR_SAME_BLOCK + stair_segment

    return _INTER_BLOCK_SECONDS + _SAME_CORRIDOR_FLOOR_SAME_BLOCK + stair_segment


def format_travel_minutes(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    mins = max(1, math.ceil(seconds / 60))
    return f"{mins} minute{'s' if mins != 1 else ''}"


def describe_room(room_meta: dict[str, Any]) -> str:
    fl = _ordinal_floor(int(room_meta["floor"]))
    return (
        f"{room_meta['code']} ({room_meta['title']}), "
        f"{room_meta['block']} Block, {fl} Floor"
    )


def shortest_path_hints(from_room_field: str | None, to_room_field: str | None) -> list[str]:
    """Plain-language steps — no maps API."""
    a = resolve_room(from_room_field)
    b = resolve_room(to_room_field)
    if not a or not b:
        return ["Room not found in campus directory — check the room code in your timetable."]
    if _normalize_code_key(a["code"]) == _normalize_code_key(b["code"]):
        return ["You are already at this room."]

    steps: list[str] = []

    def block_label(nm: str) -> str:
        return f"{nm} Block"

    if a["block"] != b["block"]:
        steps.append(f"Leave {block_label(a['block'])} from near {a['code']}.")
        steps.append(f"Walk to {block_label(b['block'])} main entrance (campus connectors).")

    af, bf = int(a["floor"]), int(b["floor"])
    if af != bf:
        if af > bf:
            steps.append(f"Take the stairs down {_ordinal_floor(af)} → {_ordinal_floor(bf)} in {block_label(b['block'])}.")
        else:
            steps.append(f"Take the stairs up {_ordinal_floor(af)} → {_ordinal_floor(bf)} in {block_label(b['block'])}.")

    steps.append(f"Locate {b['code']} ({b['title']}) on the {_ordinal_floor(bf)} floor.")
    return steps


_TIME_SPLIT = re.compile(
    r"""
    (?P<start>\d{1,2}:\d{2}(?:\s*[AaPp][Mm])?)
    \s*
    [-–—to]+\s*
    (?P<end>\d{1,2}:\d{2}(?:\s*[AaPp][Mm])?)
    """,
    re.VERBOSE,
)


def _parse_clock_fragment(fragment: str) -> tuple[int, int, bool]:
    frag = fragment.strip().upper().replace(" ", "")
    hour12 = False
    if frag.endswith("AM") or frag.endswith("PM"):
        hour12 = True
        mer = frag[-2:]
        frag = frag[:-2].strip()
    h_str, m_str = frag.split(":", 1)
    h = int(h_str)
    m = int(m_str)
    if hour12:
        if mer == "PM" and h != 12:
            h += 12
        if mer == "AM" and h == 12:
            h = 0
    return h, m, hour12


def parse_class_window(
    time_cell: str | None,
    day: datetime,
) -> tuple[datetime, datetime] | None:
    """Parse timetable `time` text into start/end datetimes on the given calendar day."""
    if not time_cell or not time_cell.strip():
        return None
    s = time_cell.strip()
    m = _TIME_SPLIT.search(s.replace(" ", " "))
    if not m:
        return None

    sh, sm, _ = _parse_clock_fragment(m.group("start"))
    eh, em, _ = _parse_clock_fragment(m.group("end"))
    day_d = day.date()

    start = datetime.combine(day_d, time(sh, sm))
    end = datetime.combine(day_d, time(eh, em))

    # Overnight session (uncommon)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def normalize_weekday(day_name: str | None) -> str | None:
    if not day_name:
        return None
    k = day_name.strip().lower()
    if k in _WEEKDAY_ALIASES:
        return _WEEKDAY_ALIASES[k]
    # Title-case match Monday, Tuesday, ...
    canon = day_name.strip().capitalize()
    days = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
    for d in days:
        if canon.lower() == d.lower():
            return d
    return None


@dataclass
class ParsedSession:
    row_id: int | None
    day: str
    subject: str
    room: str
    start: datetime
    end: datetime
    raw_time: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.row_id,
            "day": self.day,
            "class_name": self.subject,
            "time": self.raw_time,
            "room": self.room,
            "start_iso": self.start.isoformat(timespec="minutes"),
            "end_iso": self.end.isoformat(timespec="minutes"),
        }


def iter_parsed_sessions_for_day(
    schedule_rows: Sequence[Any],
    day: datetime,
) -> list[ParsedSession]:
    """Filter sqlite Row / dict schedules to a weekday and parse clocks."""
    want = normalize_weekday(day.strftime("%A"))
    out: list[ParsedSession] = []
    for row in schedule_rows:
        rd = normalize_weekday(_row_get(row, "day"))
        if rd != want:
            continue
        time_cell = _row_get(row, "time")
        win = parse_class_window(time_cell, day)
        if not win:
            continue
        start, end = win
        rid = _row_get(row, "id")
        out.append(
            ParsedSession(
                row_id=int(rid) if rid is not None else None,
                day=_row_get(row, "day") or "",
                subject=_row_get(row, "class_name") or "",
                room=_row_get(row, "room") or "",
                start=start,
                end=end,
                raw_time=time_cell or "",
            )
        )
    out.sort(key=lambda s: s.start)
    return out


def _row_get(row: Any, key: str) -> Any:
    try:
        return row[key]
    except Exception:
        return getattr(row, key, None)


@dataclass
class NavigationBrief:
    now: datetime
    weekday_label: str
    sessions: list[ParsedSession]
    current: ParsedSession | None
    next_session: ParsedSession | None
    previous: ParsedSession | None
    travel_seconds: int | None
    travel_message: str | None
    path_hints: list[str]
    late_for_next: bool
    urgency_message: str | None


def build_navigation_brief(schedule_rows: Sequence[Any], now: datetime | None = None) -> NavigationBrief:
    now = now or datetime.now()
    day_sessions = iter_parsed_sessions_for_day(schedule_rows, now)
    weekday_label = normalize_weekday(now.strftime("%A")) or now.strftime("%A")

    current = None
    next_session = None
    previous = None

    for s in day_sessions:
        if s.start <= now < s.end:
            current = s
        elif s.start > now and next_session is None:
            next_session = s
        if s.end <= now:
            previous = s

    travel_seconds = None
    travel_message = None
    path_hints = []
    late_for_next = False
    urgency_message = None

    if next_session:
        origin_room = None
        if current:
            origin_room = current.room
        elif previous:
            origin_room = previous.room

        if origin_room:
            travel_seconds = estimate_travel_seconds(origin_room, next_session.room)
            dest = resolve_room(next_session.room)
            dest_txt = describe_room(dest) if dest else f"Room {next_session.room} (unknown in campus catalog)"
            if travel_seconds is not None:
                fmt = format_travel_minutes(travel_seconds)
                travel_message = f"Next class: {next_session.subject} at {dest_txt}. Estimated time to reach: {fmt}."
            if origin_room:
                path_hints = shortest_path_hints(origin_room, next_session.room)

            if travel_seconds is not None:
                secs_until = (next_session.start - now).total_seconds()
                if secs_until < travel_seconds:
                    late_for_next = True
                    urgency_message = (
                        "You may arrive after the bell — leave immediately or notify your lecturer."
                    )
                elif secs_until < travel_seconds + 120:
                    urgency_message = "Leave soon — you have under two minutes of buffer after travel time."

        else:
            dest = resolve_room(next_session.room)
            if dest:
                travel_message = (
                    f"Next class: {next_session.subject} at {describe_room(dest)} "
                    "(set an earlier session in your timetable for travel estimation)."
                )

    elif current:
        urgency_message = "No further classes parsed for today after this slot."

    return NavigationBrief(
        now=now,
        weekday_label=weekday_label,
        sessions=[*day_sessions],
        current=current,
        next_session=next_session,
        previous=previous,
        travel_seconds=travel_seconds,
        travel_message=travel_message,
        path_hints=path_hints,
        late_for_next=late_for_next,
        urgency_message=urgency_message,
    )


def briefly_list_sessions(day_sessions: Iterable[ParsedSession], limit: int = 12) -> str:
    lines = []
    for s in list(day_sessions)[:limit]:
        lines.append(f"• {_format_session_line(s)}")
    return "\n".join(lines)


def _format_session_line(s: ParsedSession) -> str:
    return (
        f"{s.subject}: {s.start.strftime('%H:%M')}–{s.end.strftime('%H:%M')} "
        f"{s.room} ({normalize_weekday(s.day) or s.day})"
    )


from __future__ import annotations

import datetime as _dt
import os
import sqlite3
from typing import Any, Dict, List, Optional, Text

from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher


DB_PATH = os.environ.get("SMART_CAMPUS_DB", "/app/database.db")


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _meta(tracker: Tracker) -> Dict[str, Any]:
    # Metadata is sent by the Flask proxy as part of the REST webhook payload.
    latest = tracker.latest_message or {}
    return latest.get("metadata") or {}


def _role(tracker: Tracker) -> str:
    return str(_meta(tracker).get("role") or "guest").lower()


def _user_id(tracker: Tracker) -> Optional[int]:
    raw = _meta(tracker).get("user_id")
    try:
        return int(raw) if raw is not None else None
    except Exception:
        return None


def _username(tracker: Tracker) -> str:
    return str(_meta(tracker).get("username") or "Guest").strip() or "Guest"


def _salutation(tracker: Tracker) -> str:
    role = _role(tracker)
    name = _username(tracker)
    if role == "teacher":
        return f"Professor {name}"
    if role == "student":
        return f"Student {name}"
    if role == "admin":
        return f"Administrator {name}"
    return name


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table_name,))
    return cur.fetchone() is not None


def _normalize_day(s: str) -> str:
    s = (s or "").strip().lower()
    m = {
        "mon": "monday",
        "monday": "monday",
        "tue": "tuesday",
        "tues": "tuesday",
        "tuesday": "tuesday",
        "wed": "wednesday",
        "wednesday": "wednesday",
        "thu": "thursday",
        "thur": "thursday",
        "thurs": "thursday",
        "thursday": "thursday",
        "fri": "friday",
        "friday": "friday",
        "sat": "saturday",
        "saturday": "saturday",
        "sun": "sunday",
        "sunday": "sunday",
        "today": _dt.date.today().strftime("%A").lower(),
        "tomorrow": (_dt.date.today() + _dt.timedelta(days=1)).strftime("%A").lower(),
    }
    return m.get(s, s)


def _day_from_text(text: str) -> Optional[str]:
    t = (text or "").lower()
    if "tomorrow" in t:
        return _normalize_day("tomorrow")
    if "today" in t:
        return _normalize_day("today")
    for d in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]:
        if d in t:
            return d
    for d in ["mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun"]:
        if f" {d}" in f" {t}":
            return _normalize_day(d)
    return None


def _match_day(db_day: str, wanted: str) -> bool:
    a = _normalize_day(db_day)
    b = _normalize_day(wanted)
    return a == b


def _teacher_subject_for_user(conn: sqlite3.Connection, user_id: Optional[int]) -> Optional[str]:
    if not user_id:
        return None
    cur = conn.cursor()
    cur.execute("SELECT teacher_subject FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    sub = (row["teacher_subject"] if row else None) if row else None
    sub = (sub or "").strip()
    return sub or None


class ActionGetSchedule(Action):
    def name(self) -> Text:
        return "action_get_schedule"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict[Text, Any]]:
        wanted_day = _day_from_text(tracker.latest_message.get("text") if tracker.latest_message else "")  # type: ignore[union-attr]
        role = _role(tracker)
        who = _salutation(tracker)

        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT day, class_name, time, room FROM schedules ORDER BY id ASC")
        rows = cur.fetchall()

        if not rows:
            conn.close()
            dispatcher.utter_message(
                text=f"{who}, I’m unable to find any schedule entries at the moment. Please ask an administrator or teacher to add schedules first."
            )
            return []

        filtered = rows
        label = "the schedule"
        if wanted_day:
            filtered = [r for r in rows if _match_day(str(r["day"] or ""), wanted_day)]
            label = f"your schedule for {wanted_day.title()}"

        # Teacher-specific phrasing: approximate "teaching" using teacher_subject when available
        teacher_subject = None
        if role == "teacher":
            teacher_subject = _teacher_subject_for_user(conn, _user_id(tracker))
            if teacher_subject:
                ts = teacher_subject.lower()
                filtered = [r for r in filtered if ts in str(r["class_name"] or "").lower()]

        conn.close()

        if not filtered:
            if wanted_day:
                dispatcher.utter_message(
                    text=f"{who}, you have no classes scheduled for {wanted_day.title()}. Would you like to check upcoming sessions or open your study materials?"
                )
            else:
                dispatcher.utter_message(
                    text=f"{who}, I couldn’t find any matching classes. Would you like to check today’s schedule or view the full weekly timetable?"
                )
            dispatcher.utter_message(
                buttons=[
                    {"title": "View Today", "payload": "Do I have any classes today?"},
                    {"title": "View Tomorrow", "payload": "Do I have any classes tomorrow?"},
                    {"title": "View Notes", "payload": "Show my latest notes"},
                ]
            )
            return []

        count = len(filtered)
        if role == "teacher":
            if wanted_day:
                intro = f"{who}, you are scheduled to teach {count} class{'es' if count != 1 else ''} on {wanted_day.title()}."
            else:
                intro = f"{who}, here is the class schedule I found."
        elif role == "student":
            if wanted_day:
                intro = f"{who}, you have {count} class{'es' if count != 1 else ''} scheduled on {wanted_day.title()}."
            else:
                intro = f"{who}, here is the schedule I found."
        else:
            intro = f"Here is {label}:"

        lines = [intro, ""]
        for r in filtered:
            lines.append(f"- {r['day']}: {r['class_name']} at {r['time']} in Room {r['room']}")
        dispatcher.utter_message(text="\n".join(lines).strip())
        dispatcher.utter_message(
            buttons=[
                {"title": "View Notes", "payload": "Show my latest notes"},
                {"title": "Preparation Tips", "payload": "How should I prepare for class?"},
            ]
        )
        return []


class ActionListNotes(Action):
    def name(self) -> Text:
        return "action_list_notes"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict[Text, Any]]:
        who = _salutation(tracker)
        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT subject, title, file_path FROM notes ORDER BY id DESC")
        rows = cur.fetchall()
        conn.close()

        if not rows:
            dispatcher.utter_message(
                text=f"{who}, there are no uploaded notes available at the moment. Please check again later, or contact your instructor to upload study materials."
            )
            dispatcher.utter_message(
                buttons=[
                    {"title": "View Schedule", "payload": "Do I have any classes today?"},
                    {"title": "Announcements", "payload": "Any announcements?"},
                ]
            )
            return []

        lines = [f"{who}, here are the latest uploaded notes/materials:", ""]
        for r in rows[:8]:
            if r["file_path"]:
                lines.append(f"- {r['subject']} | {r['title']} → /uploads/{r['file_path']}")
            else:
                lines.append(f"- {r['subject']} | {r['title']} → (file not attached)")
        dispatcher.utter_message(text="\n".join(lines))
        dispatcher.utter_message(
            buttons=[
                {"title": "View Schedule", "payload": "Do I have any classes today?"},
                {"title": "Preparation Tips", "payload": "How should I prepare for class?"},
            ]
        )
        return []


class ActionPreparationTips(Action):
    def name(self) -> Text:
        return "action_preparation_tips"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict[Text, Any]]:
        role = _role(tracker)
        who = _salutation(tracker)
        if role == "teacher":
            dispatcher.utter_message(
                text=(
                    f"{who}, here is a professional teaching preparation checklist:\n"
                    "1) Review schedule and room details.\n"
                    "2) Prepare 3 learning objectives.\n"
                    "3) Upload notes/resources in Notes.\n"
                    "4) Prepare 2 quick checks (questions/quiz).\n"
                    "5) Plan 5 minutes for Q&A."
                )
            )
        else:
            dispatcher.utter_message(
                text=(
                    f"{who}, here is a professional class preparation checklist:\n"
                    "1) Check schedule (day, subject, time, room).\n"
                    "2) Open uploaded notes for the subject.\n"
                    "3) Spend 20–30 minutes reviewing key concepts.\n"
                    "4) Write 3 bullet summaries + 2 questions.\n"
                    "5) Keep required materials ready before class."
                )
            )
        dispatcher.utter_message(
            buttons=[
                {"title": "View Today", "payload": "Do I have any classes today?"},
                {"title": "View Notes", "payload": "Show my latest notes"},
            ]
        )
        return []


class ActionListTeachers(Action):
    def name(self) -> Text:
        return "action_list_teachers"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict[Text, Any]]:
        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT username, COALESCE(full_name, username) AS display FROM users WHERE LOWER(role)='teacher' ORDER BY id DESC")
        rows = cur.fetchall()
        conn.close()

        if not rows:
            dispatcher.utter_message(text="No teachers are listed yet.")
            return []

        lines = ["Teachers:"]
        for r in rows[:12]:
            lines.append(f"- {r['display']}")
        lines.append("")
        lines.append("You can also open the Teachers page for the full directory.")
        dispatcher.utter_message(text="\n".join(lines))
        return []


class ActionListStudents(Action):
    def name(self) -> Text:
        return "action_list_students"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict[Text, Any]]:
        role = _role(tracker)
        if role not in {"teacher", "student", "admin"}:
            dispatcher.utter_message(text="Please log in to view student information.")
            return []

        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT username, COALESCE(full_name, username) AS display FROM users WHERE LOWER(role)='student' ORDER BY id ASC")
        rows = cur.fetchall()
        conn.close()

        if not rows:
            dispatcher.utter_message(text="No students are listed yet.")
            return []

        lines = ["Students:"]
        for r in rows[:12]:
            lines.append(f"- {r['display']}")
        lines.append("")
        lines.append("Open the Students page for the complete list.")
        dispatcher.utter_message(text="\n".join(lines))
        return []


class ActionListSubjects(Action):
    def name(self) -> Text:
        return "action_list_subjects"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict[Text, Any]]:
        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT name, code, subject_id FROM subjects ORDER BY id DESC")
        rows = cur.fetchall()
        conn.close()

        if not rows:
            dispatcher.utter_message(text="No subjects are available yet.")
            return []

        lines = ["Subjects:"]
        for r in rows[:12]:
            code = (r["code"] or "").strip()
            sid = (r["subject_id"] or "").strip()
            suffix = " ".join([p for p in [code, sid] if p])
            lines.append(f"- {r['name']}{(' (' + suffix + ')') if suffix else ''}")
        dispatcher.utter_message(text="\n".join(lines))
        return []


class ActionProfileHelp(Action):
    def name(self) -> Text:
        return "action_profile_help"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict[Text, Any]]:
        who = _salutation(tracker)
        dispatcher.utter_message(
            text=(
                f"{who}, to update your profile, open the Profile page from the sidebar.\n"
                "- Update username/full name/email/phone/bio\n"
                "- Teachers can also set their subject\n"
                "- Save to apply changes\n\n"
                "If you tell me what you want to change, I’ll guide you step-by-step."
            )
        )
        return []


class ActionListAnnouncements(Action):
    def name(self) -> Text:
        return "action_list_announcements"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict[Text, Any]]:
        who = _salutation(tracker)
        conn = _db()
        if not _table_exists(conn, "announcements"):
            conn.close()
            dispatcher.utter_message(
                text=(
                    f"{who}, I’m unable to retrieve announcements because the announcements feature is not configured in the database yet. "
                    "Please contact the administrator to enable announcements."
                )
            )
            return []

        cur = conn.cursor()
        cur.execute("SELECT title, message, created_at FROM announcements ORDER BY id DESC LIMIT 5")
        rows = cur.fetchall()
        conn.close()

        if not rows:
            dispatcher.utter_message(
                text=f"{who}, there are no announcements at the moment. Would you like to check your schedule or view your study materials?"
            )
            dispatcher.utter_message(
                buttons=[
                    {"title": "View Schedule", "payload": "Do I have any classes today?"},
                    {"title": "View Notes", "payload": "Show my latest notes"},
                ]
            )
            return []

        lines = [f"{who}, here are the latest announcements:", ""]
        for r in rows:
            title = (r["title"] or "").strip() if "title" in r.keys() else ""
            msg = (r["message"] or "").strip() if "message" in r.keys() else ""
            created = (r["created_at"] or "").strip() if "created_at" in r.keys() else ""
            head = f"- {title}" if title else "- Announcement"
            if created:
                head += f" ({created})"
            lines.append(head)
            if msg:
                lines.append(f"  {msg}")
        dispatcher.utter_message(text="\n".join(lines).strip())
        return []


class ActionListAssignments(Action):
    def name(self) -> Text:
        return "action_list_assignments"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict[Text, Any]]:
        who = _salutation(tracker)
        conn = _db()
        if not _table_exists(conn, "assignments"):
            conn.close()
            dispatcher.utter_message(
                text=(
                    f"{who}, I’m unable to retrieve assignments because the assignments feature is not configured in the database yet. "
                    "Please contact the administrator to enable assignments."
                )
            )
            return []

        cur = conn.cursor()
        cur.execute("SELECT title, due_date, subject, created_at FROM assignments ORDER BY id DESC LIMIT 8")
        rows = cur.fetchall()
        conn.close()

        if not rows:
            dispatcher.utter_message(
                text=f"{who}, I don’t see any assignments listed right now. If you expect one, please check again later or contact your instructor."
            )
            return []

        lines = [f"{who}, here are the latest assignments:", ""]
        for r in rows:
            title = (r["title"] or "").strip() if "title" in r.keys() else "Assignment"
            subject = (r["subject"] or "").strip() if "subject" in r.keys() else ""
            due = (r["due_date"] or "").strip() if "due_date" in r.keys() else ""
            parts = [title]
            if subject:
                parts.append(subject)
            if due:
                parts.append(f"Due: {due}")
            lines.append("- " + " | ".join(parts))
        dispatcher.utter_message(text="\n".join(lines).strip())
        return []

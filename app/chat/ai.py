import os
from datetime import datetime

import campus_navigation as campus_nav


def build_sara_context(conn, session) -> dict:
    cursor = conn.cursor()
    user_id = session.get("user_id")
    role = (session.get("role") or "guest").lower()
    username = session.get("username") or "Guest"

    cursor.execute("SELECT day, class_name, time, room FROM schedules ORDER BY day, time")
    schedules = cursor.fetchall()
    wd = datetime.now().strftime("%A")
    canon = campus_nav.normalize_weekday(wd)
    today_lines = []
    for row in schedules:
        if campus_nav.normalize_weekday(row["day"]) == canon:
            today_lines.append(f"{row['class_name']} {row['time']} @ {row['room']}")

    cursor.execute("SELECT subject, title FROM notes ORDER BY id DESC LIMIT 5")
    notes = [f"{n['subject']}: {n['title']}" for n in cursor.fetchall()]

    return {
        "username": username,
        "role": role,
        "schedule": "; ".join(today_lines) if today_lines else "No classes parsed for today.",
        "notes": "; ".join(notes) if notes else "No notes uploaded yet.",
    }


def ai_sara_reply(message: str, context: dict) -> str | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        system_prompt = f"""You are Sara, the Smart Campus Portal assistant.
Current user: {context['username']} (Role: {context['role']})
Today: {datetime.now().strftime('%A, %d %B %Y')}
Today's schedule: {context['schedule']}
Recent notes: {context['notes']}

Answer questions about schedules, notes, campus navigation, leave requests, and academic resources.
Be concise, friendly, and helpful. For tasks that require system actions, tell the user which page to visit."""

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": message}],
        )
        return response.content[0].text
    except Exception:
        return None

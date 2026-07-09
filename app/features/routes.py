import os
import uuid
from datetime import date, datetime

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

import database
from app.utils import fetch_subject_names, role_required, subject_is_valid
from app.validators import validate_upload_size

features_bp = Blueprint("features", __name__)

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def _get_db():
    from app.utils import get_db
    return get_db()


@features_bp.route("/announcements")
@role_required("student", "teacher", "admin")
def announcements():
    role = (session.get("role") or "").lower()
    rows = database.get_announcements_for_role(role, limit=50)
    return render_template("announcements.html", announcements=rows, role=role)


@features_bp.route("/announcements/create", methods=["POST"])
@role_required("teacher", "admin")
def create_announcement():
    title = (request.form.get("title") or "").strip()
    body = (request.form.get("body") or "").strip()
    target_role = (request.form.get("target_role") or "all").lower()
    if not title or not body:
        flash("Title and body are required.", "danger")
        return redirect(url_for("features.announcements"))
    if target_role not in {"all", "student", "teacher"}:
        target_role = "all"
    conn = _get_db()
    conn.execute(
        "INSERT INTO announcements (title, body, target_role, created_by) VALUES (?, ?, ?, ?)",
        (title, body, target_role, session["user_id"]),
    )
    conn.commit()
    database.notify_role(target_role, f"New announcement: {title}", "/announcements")
    flash("Announcement posted.", "success")
    return redirect(url_for("features.announcements"))


@features_bp.route("/attendance")
@role_required("student", "teacher", "admin")
def attendance():
    role = (session.get("role") or "").lower()
    conn = _get_db()
    cursor = conn.cursor()

    if role == "student":
        cursor.execute(
            """
            SELECT a.*, s.class_name, s.day
            FROM attendance a
            JOIN schedules s ON s.id = a.schedule_id
            WHERE a.student_id=?
            ORDER BY a.date DESC
            """,
            (session["user_id"],),
        )
        records = cursor.fetchall()
        total = len(records)
        present = sum(1 for r in records if r["status"] == "Present")
        pct = round((present / total) * 100, 1) if total else 0
        return render_template(
            "attendance.html", role=role, records=records, attendance_pct=pct
        )

    cursor.execute("SELECT * FROM schedules ORDER BY day, time")
    schedules = cursor.fetchall()
    cursor.execute("SELECT id, username, full_name FROM users WHERE LOWER(role)='student' ORDER BY username")
    students = cursor.fetchall()

    if role == "admin":
        cursor.execute(
            """
            SELECT status, COUNT(*) AS c FROM attendance GROUP BY status
            """
        )
        stats = {r["status"]: r["c"] for r in cursor.fetchall()}
        return render_template(
            "attendance.html",
            role=role,
            schedules=schedules,
            students=students,
            stats=stats,
        )

    return render_template(
        "attendance.html", role=role, schedules=schedules, students=students
    )


@features_bp.route("/attendance/mark", methods=["POST"])
@role_required("teacher")
def mark_attendance():
    schedule_id = request.form.get("schedule_id", type=int)
    att_date = (request.form.get("date") or date.today().isoformat()).strip()
    student_ids = request.form.getlist("student_id")
    conn = _get_db()
    for sid in student_ids:
        status = request.form.get(f"status_{sid}", "Present")
        conn.execute(
            """
            INSERT INTO attendance (student_id, schedule_id, date, status, marked_by)
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(sid), schedule_id, att_date, status, session["user_id"]),
        )
    conn.commit()
    flash("Attendance saved.", "success")
    return redirect(url_for("features.attendance"))


@features_bp.route("/assignments")
@role_required("student", "teacher")
def assignments():
    role = (session.get("role") or "").lower()
    conn = _get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT a.*, u.username AS teacher_name
        FROM assignments a
        LEFT JOIN users u ON u.id = a.created_by
        ORDER BY a.due_date ASC, a.id DESC
        """
    )
    items = cursor.fetchall()
    submissions = []
    submissions_by_assignment = {}
    if role == "student":
        cursor.execute(
            "SELECT * FROM submissions WHERE student_id=?",
            (session["user_id"],),
        )
        submissions = {r["assignment_id"]: r for r in cursor.fetchall()}
    elif role == "teacher":
        cursor.execute(
            """
            SELECT s.*, u.username AS student_username, u.full_name AS student_name
            FROM submissions s
            LEFT JOIN users u ON u.id = s.student_id
            ORDER BY s.submitted_at DESC
            """
        )
        for r in cursor.fetchall():
            submissions_by_assignment.setdefault(r["assignment_id"], []).append(r)
    subjects = fetch_subject_names(cursor)
    return render_template(
        "assignments.html",
        assignments=items,
        role=role,
        submissions=submissions,
        submissions_by_assignment=submissions_by_assignment,
        subjects=subjects,
    )


@features_bp.route("/assignments/create", methods=["POST"])
@role_required("teacher")
def create_assignment():
    title = (request.form.get("title") or "").strip()
    description = (request.form.get("description") or "").strip()
    subject = (request.form.get("subject") or "").strip()
    due_date = (request.form.get("due_date") or "").strip()
    if not title or not due_date:
        flash("Title and due date are required.", "danger")
        return redirect(url_for("features.assignments"))
    if not subject:
        flash("Please select a subject.", "danger")
        return redirect(url_for("features.assignments"))
    conn = _get_db()
    cursor = conn.cursor()
    if not subject_is_valid(cursor, subject):
        flash("Please select a valid subject from the list.", "danger")
        return redirect(url_for("features.assignments"))
    conn.execute(
        """
        INSERT INTO assignments (title, description, subject, due_date, created_by)
        VALUES (?, ?, ?, ?, ?)
        """,
        (title, description, subject, due_date, session["user_id"]),
    )
    conn.commit()
    database.notify_role("student", f"New assignment: {title}", "/assignments")
    flash("Assignment created.", "success")
    return redirect(url_for("features.assignments"))


@features_bp.route("/assignments/<int:assignment_id>/submit", methods=["POST"])
@role_required("student")
def submit_assignment(assignment_id):
    if not validate_upload_size(request.content_length, MAX_UPLOAD_BYTES):
        flash("File too large. Max 5MB.", "danger")
        return redirect(url_for("features.assignments"))
    file = request.files.get("file")
    if not file or not file.filename:
        flash("Please upload a file.", "danger")
        return redirect(url_for("features.assignments"))
    filename = f"{uuid.uuid4()}_{secure_filename(file.filename)}"
    folder = os.path.join("uploads", "submissions")
    os.makedirs(folder, exist_ok=True)
    file.save(os.path.join(folder, filename))
    conn = _get_db()
    conn.execute(
        """
        INSERT INTO submissions (assignment_id, student_id, file_path)
        VALUES (?, ?, ?)
        """,
        (assignment_id, session["user_id"], f"submissions/{filename}"),
    )
    conn.commit()
    flash("Submission uploaded.", "success")
    return redirect(url_for("features.assignments"))


@features_bp.route("/assignments/<int:submission_id>/grade", methods=["POST"])
@role_required("teacher")
def grade_submission(submission_id):
    grade = (request.form.get("grade") or "").strip()
    feedback = (request.form.get("feedback") or "").strip()
    conn = _get_db()
    conn.execute(
        "UPDATE submissions SET grade=?, feedback=? WHERE id=?",
        (grade, feedback, submission_id),
    )
    conn.commit()
    flash("Grade saved.", "success")
    return redirect(url_for("features.assignments"))


@features_bp.route("/messages")
@role_required("student", "teacher", "admin")
def messages():
    conn = _get_db()
    cursor = conn.cursor()
    uid = session["user_id"]
    cursor.execute(
        """
        SELECT m.*, s.username AS sender_name, r.username AS receiver_name
        FROM messages m
        JOIN users s ON s.id = m.sender_id
        JOIN users r ON r.id = m.receiver_id
        WHERE m.sender_id=? OR m.receiver_id=?
        ORDER BY m.sent_at DESC
        """,
        (uid, uid),
    )
    inbox = cursor.fetchall()
    cursor.execute(
        "SELECT id, username, role FROM users WHERE id!=? AND COALESCE(is_active,1)=1 ORDER BY role, username",
        (uid,),
    )
    users = cursor.fetchall()
    subjects = fetch_subject_names(cursor)
    role = (session.get("role") or "").lower()
    return render_template("messages.html", messages=inbox, users=users, subjects=subjects, role=role)


@features_bp.route("/messages/send", methods=["POST"])
@role_required("student", "teacher", "admin")
def send_message():
    role = (session.get("role") or "").lower()
    receiver_id = request.form.get("receiver_id", type=int)
    subject = (request.form.get("subject") or "").strip()
    body = (request.form.get("body") or "").strip()
    if not receiver_id or not body:
        flash("Recipient and message body are required.", "danger")
        return redirect(url_for("features.messages"))
    conn = _get_db()
    cursor = conn.cursor()
    if role in {"teacher", "admin"} and not subject:
        flash("Please select a subject.", "danger")
        return redirect(url_for("features.messages"))
    if subject and not subject_is_valid(cursor, subject):
        flash("Please select a valid subject from the list.", "danger")
        return redirect(url_for("features.messages"))
    conn.execute(
        "INSERT INTO messages (sender_id, receiver_id, subject, body) VALUES (?, ?, ?, ?)",
        (session["user_id"], receiver_id, subject, body),
    )
    conn.commit()
    database.create_notification(receiver_id, f"New message: {subject or 'No subject'}", "/messages")
    flash("Message sent.", "success")
    return redirect(url_for("features.messages"))


@features_bp.route("/messages/<int:message_id>/read", methods=["POST"])
@role_required("student", "teacher", "admin")
def mark_message_read(message_id):
    conn = _get_db()
    conn.execute(
        "UPDATE messages SET is_read=1 WHERE id=? AND receiver_id=?",
        (message_id, session["user_id"]),
    )
    conn.commit()
    return redirect(url_for("features.messages"))


@features_bp.route("/events")
@role_required("student", "teacher", "admin")
def events():
    conn = _get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM events ORDER BY event_date ASC")
    rows = cursor.fetchall()
    events_data = [
        {
            "title": r["title"],
            "start": r["event_date"],
            "extendedProps": {"type": r["event_type"]},
        }
        for r in rows
    ]
    role = (session.get("role") or "").lower()
    return render_template("events.html", events_json=events_data, role=role)


@features_bp.route("/events/create", methods=["POST"])
@role_required("admin")
def create_event():
    title = (request.form.get("title") or "").strip()
    description = (request.form.get("description") or "").strip()
    event_date = (request.form.get("event_date") or "").strip()
    event_type = (request.form.get("event_type") or "holiday").strip()
    if not title or not event_date:
        flash("Title and date are required.", "danger")
        return redirect(url_for("features.events"))
    conn = _get_db()
    conn.execute(
        """
        INSERT INTO events (title, description, event_date, event_type, created_by)
        VALUES (?, ?, ?, ?, ?)
        """,
        (title, description, event_date, event_type, session["user_id"]),
    )
    conn.commit()
    database.notify_role("all", f"New event: {title}", "/events")
    flash("Event created.", "success")
    return redirect(url_for("features.events"))


@features_bp.route("/notifications")
@role_required("student", "teacher", "admin")
def notifications():
    conn = _get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 50",
        (session["user_id"],),
    )
    rows = cursor.fetchall()
    conn.execute(
        "UPDATE notifications SET is_read=1 WHERE user_id=? AND is_read=0",
        (session["user_id"],),
    )
    conn.commit()
    return render_template("notifications.html", notifications=rows)


@features_bp.route("/grades")
@role_required("student", "teacher")
def grades():
    role = (session.get("role") or "").lower()
    conn = _get_db()
    cursor = conn.cursor()

    if role == "student":
        cursor.execute(
            "SELECT * FROM grades WHERE student_id=? ORDER BY recorded_at DESC",
            (session["user_id"],),
        )
        rows = cursor.fetchall()
        gpa = 0.0
        if rows:
            total_pct = sum(
                (r["marks_obtained"] / r["total_marks"]) * 100
                for r in rows
                if r["total_marks"]
            )
            gpa = round(total_pct / len(rows) / 25, 2)
        return render_template("grades.html", grades=rows, role=role, gpa=gpa)

    cursor.execute(
        """
        SELECT g.*, u.username AS student_name
        FROM grades g
        JOIN users u ON u.id = g.student_id
        ORDER BY g.recorded_at DESC
        """
    )
    rows = cursor.fetchall()
    cursor.execute("SELECT id, username FROM users WHERE LOWER(role)='student' ORDER BY username")
    students = cursor.fetchall()
    subjects = fetch_subject_names(cursor)
    return render_template("grades.html", grades=rows, role=role, students=students, subjects=subjects)


@features_bp.route("/grades/add", methods=["POST"])
@role_required("teacher")
def add_grade():
    student_id = request.form.get("student_id", type=int)
    subject = (request.form.get("subject") or "").strip()
    assignment_name = (request.form.get("assignment_name") or "").strip()
    marks = request.form.get("marks_obtained", type=float)
    total = request.form.get("total_marks", type=float)
    if not student_id or not subject or marks is None or not total:
        flash("All grade fields are required.", "danger")
        return redirect(url_for("features.grades"))
    conn = _get_db()
    cursor = conn.cursor()
    if not subject_is_valid(cursor, subject):
        flash("Please select a valid subject from the list.", "danger")
        return redirect(url_for("features.grades"))
    pct = (marks / total) * 100 if total else 0
    letter = "A" if pct >= 90 else "B" if pct >= 80 else "C" if pct >= 70 else "D" if pct >= 60 else "F"
    conn.execute(
        """
        INSERT INTO grades (student_id, subject, assignment_name, marks_obtained, total_marks, grade_letter, recorded_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (student_id, subject, assignment_name, marks, total, letter, session["user_id"]),
    )
    conn.commit()
    database.create_notification(student_id, f"Grade posted for {subject}", "/grades")
    flash("Grade recorded.", "success")
    return redirect(url_for("features.grades"))

from flask import send_from_directory, Flask, render_template, request, jsonify, redirect, url_for, session, flash, Response
import os
import database
import sqlite3
import uuid 
from datetime import date
from werkzeug.utils import secure_filename
import csv
import io

app = Flask(__name__)
app.secret_key = "smartcampus_secret_key"
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

def require_role(*roles):
    if "user_id" not in session:
        return False
    current = (session.get("role") or "").lower()
    return current in {r.lower() for r in roles}


def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn


def allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS

# =========================
# SARA AI
# =========================
@app.route("/sara_ai", methods=["POST"])
def sara_ai():
    data = request.get_json() or {}
    message = data.get("message", "").strip()
    message_lower = message.lower()

    user_name = session.get("username", "Student")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT day, class_name, time, room FROM schedules ORDER BY id ASC")
    schedules = cursor.fetchall()
    cursor.execute("SELECT subject, title, file_path FROM notes ORDER BY id DESC")
    notes = cursor.fetchall()
    conn.close()

    greeting_keywords = ("hello", "hi", "hey", "good morning", "good afternoon", "good evening")
    schedule_keywords = ("schedule", "routine", "timetable", "class timing", "my class")
    notes_keywords = ("note", "notes", "material", "study", "resource", "pdf")
    prep_keywords = ("prepare", "revision", "ready for class", "how to prepare")

    if any(word in message_lower for word in greeting_keywords):
        reply = (
            f"Hello {user_name}. I am Sara, your academic assistant. "
            "I can help with schedules, class preparation, and uploaded notes."
        )

    elif any(word in message_lower for word in schedule_keywords):
        if not schedules:
            reply = (
                "I could not find any schedule entries yet. "
                "Please ask your teacher to add classes in Schedule Management."
            )
        else:
            lines = ["Here is your current class schedule:"]
            for sch in schedules:
                lines.append(
                    f"- {sch['day']}: {sch['class_name']} at {sch['time']} in Room {sch['room']}"
                )

            prep_line = (
                "Preparation tip: review the related notes before each class, "
                "arrive 10 minutes early, and list 2 questions to ask in class."
            )
            reply = "\n".join(lines + ["", prep_line])

    elif any(word in message_lower for word in notes_keywords):
        if not notes:
            reply = (
                "No uploaded notes are available right now. "
                "Please check later or ask your teacher to upload materials."
            )
        else:
            lines = ["Here are the latest uploaded notes and resources:"]
            for note in notes[:8]:
                if note["file_path"]:
                    lines.append(
                        f"- {note['subject']} | {note['title']} -> /uploads/{note['file_path']}"
                    )
                else:
                    lines.append(
                        f"- {note['subject']} | {note['title']} -> file not attached yet"
                    )
            lines.append("")
            lines.append(
                "Study professionally: preview objectives first, read actively, "
                "then summarize key points in your own words."
            )
            reply = "\n".join(lines)

    elif any(word in message_lower for word in prep_keywords):
        reply = (
            "Professional class preparation checklist:\n"
            "1) Check tomorrow's schedule (day, subject, time, room).\n"
            "2) Open the uploaded notes for each subject.\n"
            "3) Spend 20-30 minutes reviewing key concepts.\n"
            "4) Write quick revision bullets and at least 2 doubts.\n"
            "5) Keep required materials ready before class."
        )

    elif "teacher" in message_lower or "faculty" in message_lower:
        reply = (
            "You can view the full faculty directory in the Teachers section. "
            "If you need, ask me for schedule and preparation guidance too."
        )

    elif "hobby" in message_lower or "interest" in message_lower:
        reply = (
            "You can manage student interests in the Hobbies section. "
            "Balanced academics plus interests improves long-term performance."
        )

    else:
        reply = (
            "I can assist professionally with:\n"
            "- Your class schedule (day, subject, time, room)\n"
            "- Uploaded notes and study resources\n"
            "- Class preparation plans\n\n"
            "Try asking: 'What is my schedule?' or 'How should I prepare for tomorrow's classes?'"
        )

    return jsonify({"reply": reply})

# =========================
# HOME (FIXED)
# =========================
@app.route("/")
def home():
    return render_template("home.html")


# =========================
# SIGNUP
# =========================
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        role = request.form["role"]

        success = database.add_user(username, password, role)

        if success:
            flash("Account created successfully!", "success")
            return redirect(url_for("login"))
        else:
            flash("User already exists!", "danger")

    return render_template("signup.html")


# =========================
# LOGIN (FIXED)
# =========================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        role = request.form["role"].lower()

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT * FROM users 
            WHERE username=? AND password=? AND role=? AND COALESCE(is_active, 1)=1
            """,
            (username, password, role),
        )

        user = cursor.fetchone()
        conn.close()

        if user:
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]

            if role == "teacher":
                return redirect("/teacher_dashboard")
            elif role == "admin":
                return redirect("/admin_dashboard")
            else:
                return redirect("/student_dashboard")

        flash("Invalid credentials", "danger")

    return render_template("login.html")


# =========================
# ADMIN LOGIN (PRIVATE)
# =========================
@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = (request.form.get("username", "") or "").strip()
        password = (request.form.get("password", "") or "").strip()

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM users WHERE username=? AND password=? AND LOWER(role)='admin' AND COALESCE(is_active, 1)=1",
            (username, password),
        )
        user = cursor.fetchone()
        conn.close()

        if user:
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            return redirect("/admin_dashboard")

        flash("Invalid admin credentials.", "danger")

    return render_template("admin_login.html")


# =========================
# profile
# =========================
@app.route("/profile", methods=["GET", "POST"])
def profile():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM subjects ORDER BY name ASC")
    subjects = [row["name"] for row in cursor.fetchall() if row["name"]]

    if request.method == "POST":
        new_username = request.form.get("username", "").strip()
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        bio = request.form.get("bio", "").strip()
        teacher_subject = request.form.get("teacher_subject", "").strip()
        profile_image = request.files.get("profile_image")

        if not new_username:
            flash("Username is required.", "danger")
            conn.close()
            return redirect(url_for("profile"))

        cursor.execute(
            "SELECT id FROM users WHERE username = ? AND id != ?",
            (new_username, session["user_id"]),
        )
        duplicate_user = cursor.fetchone()
        if duplicate_user:
            flash("Username already exists. Please choose another.", "danger")
            conn.close()
            return redirect(url_for("profile"))

        image_path_to_save = None
        if profile_image and profile_image.filename:
            if not allowed_image(profile_image.filename):
                flash("Invalid image format. Use png, jpg, jpeg, gif, or webp.", "danger")
                conn.close()
                return redirect(url_for("profile"))

            cursor.execute("SELECT profile_image FROM users WHERE id=?", (session["user_id"],))
            old_user = cursor.fetchone()
            old_image = old_user["profile_image"] if old_user else None

            filename = f"{uuid.uuid4()}_{secure_filename(profile_image.filename)}"
            upload_folder = os.path.join("uploads", "profile_images")
            os.makedirs(upload_folder, exist_ok=True)
            profile_image.save(os.path.join(upload_folder, filename))
            image_path_to_save = f"profile_images/{filename}"

            if old_image:
                old_image_full_path = os.path.join("uploads", old_image)
                if os.path.exists(old_image_full_path):
                    os.remove(old_image_full_path)

        if image_path_to_save:
            cursor.execute(
                """
                UPDATE users
                SET username=?, full_name=?, email=?, phone=?, bio=?, teacher_subject=?, profile_image=?
                WHERE id=?
                """,
                (new_username, full_name, email, phone, bio, teacher_subject, image_path_to_save, session["user_id"]),
            )
        else:
            cursor.execute(
                """
                UPDATE users
                SET username=?, full_name=?, email=?, phone=?, bio=?, teacher_subject=?
                WHERE id=?
                """,
                (new_username, full_name, email, phone, bio, teacher_subject, session["user_id"]),
            )

        conn.commit()
        session["username"] = new_username
        flash("Profile updated successfully.", "success")
        conn.close()
        return redirect(url_for("profile"))

    cursor.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],))
    user = cursor.fetchone()
    conn.close()

    if not user:
        session.clear()
        flash("User not found. Please login again.", "danger")
        return redirect(url_for("login"))

    return render_template("profile.html", user=user, subjects=subjects)

# =========================
# add hobby
# =========================
@app.route("/add_hobby", methods=["POST"])
def add_hobby():
    if session.get("role") != "student":
        return redirect(url_for("login"))

    raw = (request.form.get("hobby") or "").strip()
    if not raw:
        flash("Please enter at least one hobby/interest.", "danger")
        return redirect(url_for("student_hobbies"))

    # Allow comma/newline separated entries
    parts = [p.strip() for p in raw.replace("\n", ",").split(",")]
    hobbies = []
    seen = set()
    for p in parts:
        if not p:
            continue
        norm = p.lower()
        if norm in seen:
            continue
        seen.add(norm)
        hobbies.append(p[:60])

    if not hobbies:
        flash("Please enter at least one valid hobby/interest.", "danger")
        return redirect(url_for("student_hobbies"))

    student_id = session.get("user_id")
    conn = get_db()
    cursor = conn.cursor()

    # prevent duplicates per student (case-insensitive)
    cursor.execute(
        "SELECT hobby FROM student_hobbies WHERE student_id=?",
        (student_id,),
    )
    existing = {str(r["hobby"]).lower() for r in cursor.fetchall() if r["hobby"]}

    inserted = 0
    for h in hobbies:
        if h.lower() in existing:
            continue
        cursor.execute(
            "INSERT INTO student_hobbies (student_id, hobby) VALUES (?, ?)",
            (student_id, h),
        )
        inserted += 1

    conn.commit()
    conn.close()

    if inserted:
        flash(f"Added {inserted} interest(s).", "success")
    else:
        flash("Those interests were already added.", "danger")
    return redirect(url_for("student_hobbies"))


@app.route("/delete_hobby/<int:hobby_id>", methods=["POST"])
def delete_hobby(hobby_id):
    if session.get("role") != "student":
        return redirect(url_for("login"))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM student_hobbies WHERE id=? AND student_id=?",
        (hobby_id, session.get("user_id")),
    )
    conn.commit()
    conn.close()
    flash("Interest removed.", "success")
    return redirect(url_for("student_hobbies"))


# =========================
# TEACHER NOTES
# =========================
@app.route("/add_note", methods=["POST"])
def add_note():
    if session.get("role") != "teacher":
        return redirect(url_for("login"))

    subject = request.form.get("subject")
    title = request.form.get("title")
    file = request.files.get("file")   # ✅ FIXED

    filename = None

    if not file or file.filename == "":
        flash("Please upload a file for this note.", "danger")
        return redirect(url_for("notes"))

    filename = str(uuid.uuid4()) + "_" + secure_filename(file.filename)
    upload_folder = "uploads"
    os.makedirs(upload_folder, exist_ok=True)
    file.save(os.path.join(upload_folder, filename))

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO notes (subject, title, file_path)
        VALUES (?, ?, ?)
    """, (subject, title, filename))   # ✅ SAVE filename

    conn.commit()
    conn.close()

    return redirect(url_for("notes"))   # better UX


# =========================
# UPLOAD FILES
# =========================

@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory("uploads", filename)


# =========================
# DELETE NOTES
# =========================
@app.route("/delete_note/<int:note_id>")
def delete_note(note_id):
    if session.get("role") != "teacher":
        return redirect(url_for("login"))

    conn = get_db()
    cursor = conn.cursor()

    # GET FILE NAME FIRST
    cursor.execute("SELECT file_path FROM notes WHERE id=?", (note_id,))
    note = cursor.fetchone()

    if note and note["file_path"]:
        file_path = os.path.join("uploads", note["file_path"])
        if os.path.exists(file_path):
            os.remove(file_path)

    # DELETE FROM DATABASE
    cursor.execute("DELETE FROM notes WHERE id=?", (note_id,))
    conn.commit()
    conn.close()

    return redirect(url_for("notes"))

# =========================
# DASHBOARDS
# =========================
@app.route("/student_dashboard")
def student_dashboard():
    if session.get("role") != "student":
        return redirect(url_for("login"))
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM schedules ORDER BY id DESC")
    schedules = cursor.fetchall()
    conn.close()
    return render_template("dashboard.html", schedules=schedules)


# =========================
# STUDENTS
# =========================
@app.route("/students")
def students():
    if session.get("role") not in ["student", "teacher"]:
        return redirect(url_for("login"))
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE LOWER(role)=? ORDER BY id ASC", ("student",))
    students = cursor.fetchall()
    conn.close()
    return render_template("students.html", students=students)


# =========================
# TEACHERS
# =========================
@app.route("/teachers")
def teachers():
    if session.get("role") not in ["student", "teacher"]:
        return redirect(url_for("login"))
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE LOWER(role)=? ORDER BY id DESC", ("teacher",))
    teachers = cursor.fetchall()
    conn.close()
    return render_template("teachers.html", teachers=teachers)


# =========================
# SUBJECTS
# =========================
@app.route("/subjects")
def subjects():
    if session.get("role") not in ["student", "teacher"]:
        return redirect(url_for("login"))

    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM subjects")
    subjects = cur.fetchall()

    conn.close()

    return render_template("subjects.html", subjects=subjects)

# =========================
# ADD SUBJECT
# =========================
@app.route("/add_subject", methods=["POST"])
def add_subject():
    if session.get("role") != "teacher":
        return redirect(url_for("login"))

    name = request.form.get("name")
    code = request.form.get("code")
    subject_id = request.form.get("subject_id")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO subjects (name, code, subject_id) VALUES (?, ?, ?)",
        (name, code, subject_id)
    )

    conn.commit()
    conn.close()

    return redirect("/subjects")


# =========================
# NOTES
# =========================
@app.route("/notes")
def notes():
    if session.get("role") not in ["student", "teacher"]:
        return redirect(url_for("login"))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM notes")
    notes = cursor.fetchall()
    conn.close()

    return render_template("notes.html", notes=notes)


# =========================
# SCHEDULES
# =========================
@app.route("/schedules")
def schedules():
    if session.get("role") not in ["student", "teacher"]:
        return redirect(url_for("login"))
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM schedules ORDER BY id DESC")
    schedules = cursor.fetchall()

    edit_schedule = None
    edit_id = request.args.get("edit_id", type=int)
    if session.get("role") == "teacher" and edit_id:
        cursor.execute("SELECT * FROM schedules WHERE id=?", (edit_id,))
        edit_schedule = cursor.fetchone()

    conn.close()
    return render_template("schedules.html", schedules=schedules, edit_schedule=edit_schedule)


@app.route("/add_schedule", methods=["POST"])
def add_schedule():
    if session.get("role") != "teacher":
        return redirect(url_for("login"))

    day = request.form.get("day", "").strip()
    class_name = request.form.get("class_name", "").strip()
    time = request.form.get("time", "").strip()
    room = request.form.get("room", "").strip()

    if not day or not class_name or not time or not room:
        flash("All schedule fields are required.", "danger")
        return redirect(url_for("schedules"))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO schedules (day, class_name, time, room) VALUES (?, ?, ?, ?)",
        (day, class_name, time, room),
    )
    conn.commit()
    conn.close()
    flash("Schedule created successfully.", "success")
    return redirect(url_for("schedules"))


@app.route("/update_schedule/<int:schedule_id>", methods=["POST"])
def update_schedule(schedule_id):
    if session.get("role") != "teacher":
        return redirect(url_for("login"))

    day = request.form.get("day", "").strip()
    class_name = request.form.get("class_name", "").strip()
    time = request.form.get("time", "").strip()
    room = request.form.get("room", "").strip()

    if not day or not class_name or not time or not room:
        flash("All schedule fields are required.", "danger")
        return redirect(url_for("schedules", edit_id=schedule_id))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE schedules SET day=?, class_name=?, time=?, room=? WHERE id=?",
        (day, class_name, time, room, schedule_id),
    )
    conn.commit()
    conn.close()
    flash("Schedule updated successfully.", "success")
    return redirect(url_for("schedules"))


@app.route("/delete_schedule/<int:schedule_id>", methods=["POST"])
def delete_schedule(schedule_id):
    if session.get("role") != "teacher":
        return redirect(url_for("login"))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
    conn.commit()
    conn.close()
    flash("Schedule deleted successfully.", "success")
    return redirect(url_for("schedules"))


# =========================
# STUDENT HOBBIES
# =========================
@app.route("/student_hobbies")
def student_hobbies():
    if session.get("role") not in ["student", "teacher", "admin"]:
        return redirect(url_for("login"))
    role = (session.get("role") or "").lower()

    conn = get_db()
    cursor = conn.cursor()

    my_hobbies = []
    if role == "student":
        cursor.execute(
            """
            SELECT id, hobby
            FROM student_hobbies
            WHERE student_id=?
            ORDER BY id DESC
            """,
            (session.get("user_id"),),
        )
        my_hobbies = cursor.fetchall()

    cursor.execute(
        """
        SELECT u.username AS student_name, COALESCE(u.full_name, u.username) AS student_display_name,
               sh.hobby
        FROM student_hobbies sh
        JOIN users u ON u.id = sh.student_id
        ORDER BY u.id DESC, sh.id DESC
        """
    )
    all_rows = cursor.fetchall()

    cursor.execute(
        """
        SELECT hobby, COUNT(*) AS c
        FROM student_hobbies
        GROUP BY hobby
        ORDER BY c DESC, hobby ASC
        LIMIT 8
        """
    )
    top_interests = cursor.fetchall()

    cursor.execute("SELECT COUNT(DISTINCT student_id) AS c FROM student_hobbies")
    total_students_with_interests = cursor.fetchone()["c"]
    cursor.execute("SELECT COUNT(*) AS c FROM student_hobbies")
    total_interest_records = cursor.fetchone()["c"]

    conn.close()

    return render_template(
        "student_hobbies.html",
        role=role,
        my_hobbies=my_hobbies,
        student_hobbies=all_rows,
        top_interests=top_interests,
        total_students_with_interests=total_students_with_interests,
        total_interest_records=total_interest_records,
    )


# =========================
# TEACHER LEAVE
# =========================
@app.route("/teacher_leave")
def teacher_leave():
    if session.get("role") not in ["teacher", "student"]:
        return "Access Denied 🚫 You are not a teacher", 403

    conn = get_db()
    cursor = conn.cursor()

    # For teachers: show their own leave applications
    teacher_leave = []
    if session.get("role") == "teacher":
        cursor.execute(
            """
            SELECT tl.*, u.username AS teacher_name
            FROM teacher_leave tl
            JOIN users u ON u.id = tl.teacher_id
            WHERE tl.teacher_id=?
            ORDER BY tl.date DESC, tl.id DESC
            """,
            (session.get("user_id"),),
        )
        teacher_leave = cursor.fetchall()

    # For students (and teachers): show today's absent teachers
    today = date.today().isoformat()
    cursor.execute(
        """
        SELECT tl.id, tl.date, tl.subject, tl.reason, tl.status, u.username AS teacher_name
        FROM teacher_leave tl
        JOIN users u ON u.id = tl.teacher_id
        WHERE tl.date=?
        ORDER BY tl.id DESC
        """,
        (today,),
    )
    today_absent = cursor.fetchall()
    conn.close()

    return render_template(
        "teacher_leave.html",
        teacher_leave=teacher_leave,
        today_absent=today_absent,
        today=today,
    )


@app.route("/apply_teacher_leave", methods=["POST"])
def apply_teacher_leave():
    if session.get("role") != "teacher":
        return redirect(url_for("login"))

    leave_date = request.form.get("date", "").strip()
    subject = request.form.get("subject", "").strip()
    reason = request.form.get("reason", "").strip()

    if not leave_date or not subject or not reason:
        flash("Date, subject, and reason are required.", "danger")
        return redirect(url_for("teacher_leave"))

    status = "On Leave" if leave_date == date.today().isoformat() else "Scheduled Leave"

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO teacher_leave (teacher_id, date, subject, reason, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (session.get("user_id"), leave_date, subject, reason, status),
    )
    conn.commit()
    conn.close()

    flash("Leave application submitted.", "success")
    return redirect(url_for("teacher_leave"))


# =========================
# TEACHER DASHBOARDS
# =========================

@app.route("/teacher_dashboard")
def teacher_dashboard():
    if session.get("role") != "teacher":
        return redirect(url_for("login"))

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM notes")
    notes = cursor.fetchall()

    conn.close()

    return render_template(
        "teacher_dashboard.html",
        username=session.get("username"),
        notes=notes
    )


# =========================
# ADMIN
# =========================
@app.route("/admin_dashboard")
def admin_dashboard():
    if not require_role("admin"):
        return redirect(url_for("login"))

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) AS c FROM users")
    total_users = cursor.fetchone()["c"]
    cursor.execute("SELECT COUNT(*) AS c FROM users WHERE LOWER(role)='student'")
    total_students = cursor.fetchone()["c"]
    cursor.execute("SELECT COUNT(*) AS c FROM users WHERE LOWER(role)='teacher'")
    total_teachers = cursor.fetchone()["c"]
    cursor.execute("SELECT COUNT(*) AS c FROM users WHERE LOWER(role)='admin'")
    total_admins = cursor.fetchone()["c"]
    cursor.execute("SELECT COUNT(*) AS c FROM subjects")
    total_subjects = cursor.fetchone()["c"]
    cursor.execute("SELECT COUNT(*) AS c FROM notes")
    total_notes = cursor.fetchone()["c"]
    cursor.execute("SELECT COUNT(*) AS c FROM schedules")
    total_schedules = cursor.fetchone()["c"]
    cursor.execute("SELECT COUNT(*) AS c FROM teacher_leave")
    total_leaves = cursor.fetchone()["c"]

    conn.close()

    return render_template(
        "admin_dashboard.html",
        total_users=total_users,
        total_students=total_students,
        total_teachers=total_teachers,
        total_admins=total_admins,
        total_subjects=total_subjects,
        total_notes=total_notes,
        total_schedules=total_schedules,
        total_leaves=total_leaves,
    )


@app.route("/admin/users")
def admin_users():
    if not require_role("admin"):
        return redirect(url_for("login"))

    q = (request.args.get("q") or "").strip()
    role = (request.args.get("role") or "").strip().lower()

    conn = get_db()
    cursor = conn.cursor()

    where = []
    params = []
    if role in {"student", "teacher", "admin"}:
        where.append("LOWER(role)=?")
        params.append(role)
    if q:
        where.append("(username LIKE ? OR full_name LIKE ? OR email LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])

    sql = "SELECT * FROM users"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC"

    cursor.execute(sql, params)
    users = cursor.fetchall()
    conn.close()

    return render_template("admin_users.html", users=users, q=q, role=role)


@app.route("/admin/users/create", methods=["POST"])
def admin_create_user():
    if not require_role("admin"):
        return redirect(url_for("login"))

    username = (request.form.get("username") or "").strip()
    password = (request.form.get("password") or "").strip()
    role = (request.form.get("role") or "").strip().lower()

    if not username or not password or role not in {"student", "teacher", "admin"}:
        flash("Username, password and valid role are required.", "danger")
        return redirect(url_for("admin_users"))

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (username, password, role, is_active) VALUES (?, ?, ?, 1)",
            (username, password, role),
        )
        conn.commit()
        flash("User created.", "success")
    except Exception:
        flash("Could not create user (username may already exist).", "danger")
    finally:
        conn.close()

    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/reset_password", methods=["POST"])
def admin_reset_password(user_id):
    if not require_role("admin"):
        return redirect(url_for("login"))

    new_password = (request.form.get("password") or "").strip()
    if not new_password:
        flash("New password is required.", "danger")
        return redirect(url_for("admin_users"))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET password=? WHERE id=?", (new_password, user_id))
    conn.commit()
    conn.close()
    flash("Password updated.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/toggle_active", methods=["POST"])
def admin_toggle_user_active(user_id):
    if not require_role("admin"):
        return redirect(url_for("login"))

    if user_id == session.get("user_id"):
        flash("You cannot disable your own account.", "danger")
        return redirect(url_for("admin_users"))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COALESCE(is_active, 1) AS is_active FROM users WHERE id=?", (user_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash("User not found.", "danger")
        return redirect(url_for("admin_users"))

    new_val = 0 if row["is_active"] == 1 else 1
    cursor.execute("UPDATE users SET is_active=? WHERE id=?", (new_val, user_id))
    conn.commit()
    conn.close()
    flash("User status updated.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/role", methods=["POST"])
def admin_update_user_role(user_id):
    if not require_role("admin"):
        return redirect(url_for("login"))

    new_role = (request.form.get("role") or "").strip().lower()
    if new_role not in {"student", "teacher", "admin"}:
        flash("Invalid role selected.", "danger")
        return redirect(url_for("admin_users"))

    if user_id == session.get("user_id") and new_role != "admin":
        flash("You cannot remove your own admin role.", "danger")
        return redirect(url_for("admin_users"))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET role=? WHERE id=?", (new_role, user_id))
    conn.commit()
    conn.close()
    flash("User role updated.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
def admin_delete_user(user_id):
    if not require_role("admin"):
        return redirect(url_for("login"))

    if user_id == session.get("user_id"):
        flash("You cannot delete your own account.", "danger")
        return redirect(url_for("admin_users"))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    flash("User deleted.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/subjects")
def admin_subjects():
    if not require_role("admin"):
        return redirect(url_for("login"))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM subjects ORDER BY id DESC")
    subjects = cursor.fetchall()
    conn.close()
    return render_template("admin_subjects.html", subjects=subjects)


@app.route("/admin/subjects/add", methods=["POST"])
def admin_add_subject():
    if not require_role("admin"):
        return redirect(url_for("login"))

    name = (request.form.get("name") or "").strip()
    code = (request.form.get("code") or "").strip()
    subject_id = (request.form.get("subject_id") or "").strip()

    if not name:
        flash("Subject name is required.", "danger")
        return redirect(url_for("admin_subjects"))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO subjects (name, code, subject_id) VALUES (?, ?, ?)",
        (name, code, subject_id),
    )
    conn.commit()
    conn.close()
    flash("Subject added.", "success")
    return redirect(url_for("admin_subjects"))


@app.route("/admin/subjects/<int:subject_row_id>/delete", methods=["POST"])
def admin_delete_subject(subject_row_id):
    if not require_role("admin"):
        return redirect(url_for("login"))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM subjects WHERE id=?", (subject_row_id,))
    conn.commit()
    conn.close()
    flash("Subject deleted.", "success")
    return redirect(url_for("admin_subjects"))


@app.route("/admin/notes")
def admin_notes():
    if not require_role("admin"):
        return redirect(url_for("login"))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM notes ORDER BY id DESC")
    notes = cursor.fetchall()
    conn.close()
    return render_template("admin_notes.html", notes=notes)


@app.route("/admin/notes/<int:note_id>/delete", methods=["POST"])
def admin_delete_note(note_id):
    if not require_role("admin"):
        return redirect(url_for("login"))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT file_path FROM notes WHERE id=?", (note_id,))
    note = cursor.fetchone()

    if note and note["file_path"]:
        file_path = os.path.join("uploads", note["file_path"])
        if os.path.exists(file_path):
            os.remove(file_path)

    cursor.execute("DELETE FROM notes WHERE id=?", (note_id,))
    conn.commit()
    conn.close()
    flash("Note deleted.", "success")
    return redirect(url_for("admin_notes"))


@app.route("/admin/schedules")
def admin_schedules():
    if not require_role("admin"):
        return redirect(url_for("login"))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM schedules ORDER BY id DESC")
    schedules = cursor.fetchall()

    edit_schedule = None
    edit_id = request.args.get("edit_id", type=int)
    if edit_id:
        cursor.execute("SELECT * FROM schedules WHERE id=?", (edit_id,))
        edit_schedule = cursor.fetchone()

    conn.close()
    return render_template("admin_schedules.html", schedules=schedules, edit_schedule=edit_schedule)


@app.route("/admin/schedules/add", methods=["POST"])
def admin_add_schedule():
    if not require_role("admin"):
        return redirect(url_for("login"))

    day = request.form.get("day", "").strip()
    class_name = request.form.get("class_name", "").strip()
    time = request.form.get("time", "").strip()
    room = request.form.get("room", "").strip()

    if not day or not class_name or not time or not room:
        flash("All schedule fields are required.", "danger")
        return redirect(url_for("admin_schedules"))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO schedules (day, class_name, time, room) VALUES (?, ?, ?, ?)",
        (day, class_name, time, room),
    )
    conn.commit()
    conn.close()
    flash("Schedule created.", "success")
    return redirect(url_for("admin_schedules"))


@app.route("/admin/schedules/<int:schedule_id>/update", methods=["POST"])
def admin_update_schedule(schedule_id):
    if not require_role("admin"):
        return redirect(url_for("login"))

    day = request.form.get("day", "").strip()
    class_name = request.form.get("class_name", "").strip()
    time = request.form.get("time", "").strip()
    room = request.form.get("room", "").strip()

    if not day or not class_name or not time or not room:
        flash("All schedule fields are required.", "danger")
        return redirect(url_for("admin_schedules", edit_id=schedule_id))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE schedules SET day=?, class_name=?, time=?, room=? WHERE id=?",
        (day, class_name, time, room, schedule_id),
    )
    conn.commit()
    conn.close()
    flash("Schedule updated.", "success")
    return redirect(url_for("admin_schedules"))


@app.route("/admin/schedules/<int:schedule_id>/delete", methods=["POST"])
def admin_delete_schedule(schedule_id):
    if not require_role("admin"):
        return redirect(url_for("login"))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
    conn.commit()
    conn.close()
    flash("Schedule deleted.", "success")
    return redirect(url_for("admin_schedules"))


@app.route("/admin/leaves")
def admin_leaves():
    if not require_role("admin"):
        return redirect(url_for("login"))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT tl.*, u.username AS teacher_name
        FROM teacher_leave tl
        JOIN users u ON u.id = tl.teacher_id
        ORDER BY tl.date DESC, tl.id DESC
        """
    )
    leaves = cursor.fetchall()
    conn.close()
    return render_template("admin_leaves.html", leaves=leaves)


@app.route("/admin/leaves/<int:leave_id>/status", methods=["POST"])
def admin_update_leave_status(leave_id):
    if not require_role("admin"):
        return redirect(url_for("login"))

    status = (request.form.get("status") or "").strip()
    if status not in {"Approved", "Rejected", "Pending", "On Leave", "Scheduled Leave"}:
        flash("Invalid status.", "danger")
        return redirect(url_for("admin_leaves"))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE teacher_leave SET status=? WHERE id=?", (status, leave_id))
    conn.commit()
    conn.close()
    flash("Leave status updated.", "success")
    return redirect(url_for("admin_leaves"))


@app.route("/admin/reports")
def admin_reports():
    if not require_role("admin"):
        return redirect(url_for("login"))
    return render_template("admin_reports.html")


def _csv_response(filename: str, header: list[str], rows: list[list[str]]):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    data = buf.getvalue().encode("utf-8")
    return Response(
        data,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/admin/reports/users.csv")
def admin_export_users():
    if not require_role("admin"):
        return redirect(url_for("login"))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, username, full_name, email, phone, role, COALESCE(is_active, 1) AS is_active, created_at
        FROM users
        ORDER BY id ASC
        """
    )
    users = cursor.fetchall()
    conn.close()

    rows = []
    for u in users:
        rows.append(
            [
                str(u["id"]),
                u["username"] or "",
                u["full_name"] or "",
                u["email"] or "",
                u["phone"] or "",
                u["role"] or "",
                "1" if u["is_active"] == 1 else "0",
                str(u["created_at"] or ""),
            ]
        )

    return _csv_response(
        "users.csv",
        ["id", "username", "full_name", "email", "phone", "role", "is_active", "created_at"],
        rows,
    )


@app.route("/admin/reports/subjects.csv")
def admin_export_subjects():
    if not require_role("admin"):
        return redirect(url_for("login"))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, code, subject_id FROM subjects ORDER BY id ASC")
    subjects = cursor.fetchall()
    conn.close()

    rows = [[str(s["id"]), s["name"] or "", s["code"] or "", s["subject_id"] or ""] for s in subjects]
    return _csv_response("subjects.csv", ["id", "name", "code", "subject_id"], rows)


@app.route("/admin/backup")
def admin_backup():
    if not require_role("admin"):
        return redirect(url_for("login"))

    path = "database.db"
    if not os.path.exists(path):
        return "Database file not found", 404

    with open(path, "rb") as f:
        data = f.read()

    return Response(
        data,
        mimetype="application/octet-stream",
        headers={"Content-Disposition": "attachment; filename=database.db"},
    )


# =========================
# LOGOUT
# =========================
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# =========================
# INIT DB
# =========================
@app.route("/init-db")
def init_db():
    database.create_tables()
    return "Database created!"


if __name__ == "__main__":
    database.create_tables()

    app.run(debug=True)
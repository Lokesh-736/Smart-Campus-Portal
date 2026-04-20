from flask import send_from_directory, Flask, render_template, request, jsonify, redirect, url_for, session, flash
import os
import database
import sqlite3
import uuid 
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "smartcampus_secret_key"


def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn

# =========================
# SARA AI
# =========================
@app.route("/sara_ai", methods=["POST"])
def sara_ai():

    data = request.get_json()
    message = data.get("message", "").lower()

    if "hello" in message or "hi" in message:
        reply = "Hello 👋 I am Sara AI. How can I help you today?"

    elif "notes" in message:
        reply = "You can find all study notes in the Notes section 📚"

    elif "teacher" in message:
        reply = "Check the Teachers page to view all faculty members 👨‍🏫"

    elif "schedule" in message:
        reply = "Go to the Schedule page to see class timings ⏰"

    elif "hobby" in message:
        reply = "You can add your hobbies in Student Interests section 🎨"

    else:
        reply = "I am still learning 🤖 Try asking about notes, teachers, or schedules."

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

        cursor.execute("""
            SELECT * FROM users 
            WHERE username=? AND password=? AND role=?
        """, (username, password, role))

        user = cursor.fetchone()
        conn.close()

        if user:
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]

            if role == "teacher":
                return redirect("/teacher_dashboard")
            else:
                return redirect("/student_dashboard")

        flash("Invalid credentials", "danger")

    return render_template("login.html")


# =========================
# profile
# =========================
@app.route("/profile")
def profile():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user = {
        "id": session.get("user_id"),
        "username": session.get("username"),
        "role": session.get("role")
    }

    return render_template("profile.html", user=user)

# =========================
# add hobby
# =========================
@app.route("/add_hobby", methods=["POST"])
def add_hobby():
    if session.get("role") != "student":
        return redirect(url_for("login"))

    hobby = request.form["hobby"]
    student_id = session.get("user_id")  # IMPORTANT: must store this during login

    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute(
        "INSERT INTO student_hobbies (student_id, hobby) VALUES (?, ?)",
        (student_id, hobby)
    )

    conn.commit()
    conn.close()

    flash("Hobby added successfully!")
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

    if file and file.filename != "":
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

@app.route("/uploads/<filename>")
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
    return render_template("dashboard.html")   # FIXED


# =========================
# STUDENTS
# =========================
@app.route("/students")
def students():
    if session.get("role") not in ["student", "teacher"]:
        return redirect(url_for("login"))
    return render_template("students.html")


# =========================
# TEACHERS
# =========================
@app.route("/teachers")
def teachers():
    if session.get("role") not in ["student", "teacher"]:
        return redirect(url_for("login"))
    return render_template("teachers.html")


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
    return render_template("schedules.html")


# =========================
# STUDENT HOBBIES
# =========================
@app.route("/student_hobbies")
def student_hobbies():
    if session.get("role") not in ["student", "teacher"]:
        return redirect(url_for("login"))
    return render_template("student_hobbies.html")


# =========================
# TEACHER LEAVE
# =========================
@app.route("/teacher_leave")
def teacher_leave():
    if session.get("role") not in ["teacher", "student"]:
        return "Access Denied 🚫 You are not a teacher", 403
    return render_template("teacher_leave.html")


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
    if not os.path.exists("database.db"):
        database.create_tables()

    app.run(debug=True)
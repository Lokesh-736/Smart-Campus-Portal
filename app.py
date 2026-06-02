from flask import send_from_directory, Flask, render_template, request, jsonify, redirect, url_for, session, flash, Response, g
import os

# Load local .env (SMTP credentials, etc.) — not committed to git
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.isfile(_env_path):
    with open(_env_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _key, _, _val = _line.partition("=")
            _key, _val = _key.strip(), _val.strip().strip('"').strip("'")
            if _key and _key not in os.environ:
                os.environ[_key] = _val

import database
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from functools import wraps

import campus_navigation as campus_nav
from werkzeug.utils import secure_filename
import csv
import io
import requests
import re

import email_utils
from flask_bcrypt import Bcrypt
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect

from app.chat.ai import ai_sara_reply, build_sara_context
from app.features.routes import features_bp
from app.logging_config import setup_logging
from app.utils import fetch_subject_names, subject_is_valid
from app.validators import validate_password, validate_username

logger = setup_logging()

app = Flask(__name__)
app.register_blueprint(features_bp)
bcrypt = Bcrypt(app)
csrf = CSRFProtect(app)
limiter = Limiter(get_remote_address, app=app, default_limits=["200 per day", "50 per hour"])

EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")
app.config["WTF_CSRF_ENABLED"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

def require_role(*roles):
    if "user_id" not in session:
        return False
    current = (session.get("role") or "").lower()
    return current in {r.lower() for r in roles}


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect("database.db")
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "danger")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login"))
            current = (session.get("role") or "").lower()
            if current not in {r.lower() for r in roles}:
                flash("Access denied.", "danger")
                if current == "admin":
                    return redirect(url_for("admin_dashboard"))
                if current == "teacher":
                    return redirect(url_for("teacher_dashboard"))
                return redirect(url_for("student_dashboard"))
            return f(*args, **kwargs)
        return decorated
    return decorator


def _password_matches(stored: str, plain: str) -> bool:
    if not stored or not plain:
        return False
    if isinstance(stored, str) and stored.startswith("$2"):
        return bcrypt.check_password_hash(stored, plain)
    return stored == plain


def _upgrade_password_hash(user_id: int, plain: str) -> None:
    hashed = bcrypt.generate_password_hash(plain).decode("utf-8")
    conn = get_db()
    conn.execute("UPDATE users SET password=? WHERE id=?", (hashed, user_id))
    conn.commit()


def allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def _validate_saved_image_or_cleanup(saved_path: str) -> bool:
    try:
        with open(saved_path, "rb") as f:
            header = f.read(12)
    except OSError:
        return False
    is_image = (
        header.startswith(b"\x89PNG")
        or header[:2] == b"\xff\xd8"
        or header[:6] in (b"GIF87a", b"GIF89a")
        or (len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP")
    )
    if not is_image and os.path.exists(saved_path):
        os.remove(saved_path)
    return is_image


@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@app.context_processor
def inject_nav_counts():
    uid = session.get("user_id")
    role = session.get("role") or ""
    if not uid:
        return {"unread_count": 0, "announcements_preview": []}
    return {
        "unread_count": database.unread_notification_count(uid) + database.unread_message_count(uid),
        "announcements_preview": database.get_announcements_for_role(role, 5),
    }


# =========================
# CHAT (RASA)
# =========================
def _sara_reply_looks_like_stacked_menu(text: str | None) -> bool:
    if not text:
        return False
    lower = text.lower()
    cues = ["today's classes", "tomorrow's classes", "latest notes", "preparation tips"]
    hits = sum(1 for cue in cues if cue in lower)
    return hits >= 3


def _fallback_chat_quick_actions():
    """Short button labels → concrete questions the legacy NLP understands."""
    return [
        {"title": "Today's timetable", "payload": "Show timetable for today"},
        {"title": "Next class & travel time", "payload": "What is my next class and walking time?"},
        {"title": "Latest notes", "payload": "Show latest notes"},
        {"title": "Prep checklist", "payload": "How should I prepare for class?"},
    ]


def _chat_day_anchor(message_lower: str, ref: datetime) -> datetime:
    if "tomorrow" in message_lower:
        return ref + timedelta(days=1)
    if "next week" in message_lower:
        return ref + timedelta(days=7)
    if "yesterday" in message_lower:
        return ref - timedelta(days=1)
    return ref


def _legacy_sara_reply(message: str) -> str:
    message_lower = (message or "").lower()
    role = (session.get("role") or "guest").lower()
    user_name = (session.get("username") or "Guest").strip() or "Guest"
    if role == "teacher":
        who = f"Professor {user_name}"
    elif role == "student":
        who = f"Student {user_name}"
    elif role == "admin":
        who = f"Administrator {user_name}"
    else:
        who = user_name
    casual_name = user_name.split()[0]

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, day, class_name, time, room FROM schedules ORDER BY day ASC, time ASC")
    schedules = cursor.fetchall()
    cursor.execute("SELECT subject, title, file_path FROM notes ORDER BY id DESC")
    notes = cursor.fetchall()
    conn.close()

    greeting_keywords = ("hello", "hi", "hey", "good morning", "good afternoon", "good evening")
    schedule_keywords = (
        "schedule",
        "routine",
        "timetable",
        "time table",
        "class timing",
        "my class",
        "classes today",
        "classes tomorrow",
    )
    nav_keywords = (
        "navigation",
        "navigate",
        "walking",
        "walk time",
        "travel time",
        "next class",
        "current class",
        "route",
        "how long to",
        "time to reach",
    )
    notes_keywords = ("note", "notes", "material", "study", "resource", "pdf")
    prep_keywords = ("prepare", "revision", "ready for class", "how to prepare")

    if any(word in message_lower for word in greeting_keywords):
        return (
            f"Hey {casual_name} — Sara, your Smart Campus assistant.\n"
            "Tell me what you need in one line (timetable, next class navigation, notes, prep tips). "
            "Use the shortcuts below."
        )

    if any(word in message_lower for word in nav_keywords):
        clock = datetime.now()
        anchor = _chat_day_anchor(message_lower, clock)
        day_sessions = campus_nav.iter_parsed_sessions_for_day(schedules, anchor)
        day_label = campus_nav.normalize_weekday(anchor.strftime("%A")) or anchor.strftime("%A")
        chunks: list[str] = []

        if anchor.date() != clock.date():
            if not day_sessions:
                return f"{who}, nothing parsed for {day_label} ({anchor.strftime('%d %b')})."

            chunks.append(
                f"Planner view — {day_label}, {anchor.strftime('%d %b')} "
                "(travel estimated between consecutive sessions only)."
            )

            ordered = sorted(day_sessions, key=lambda s: s.start)
            first = ordered[0]
            m0 = campus_nav.resolve_room(first.room)
            chunks.append(
                f"Starts with {first.subject} at "
                f"{campus_nav.describe_room(m0) if m0 else first.room}: "
                f"{first.start.strftime('%H:%M')}–{first.end.strftime('%H:%M')}."
            )

            if len(ordered) > 1:
                second = ordered[1]
                m1 = campus_nav.resolve_room(second.room)
                loc1 = campus_nav.describe_room(m1) if m1 else second.room
                fmt = campus_nav.format_travel_minutes(
                    campus_nav.estimate_travel_seconds(first.room, second.room)
                )
                chunks.append(
                    f"Then {second.subject} at {loc1} ({second.start.strftime('%H:%M')}). "
                    f"Rough walk allowance: ~{fmt or '?'}."
                )
                hints = "; ".join(campus_nav.shortest_path_hints(first.room, second.room)[:3])
                if hints:
                    chunks.append(hints)

            return "\n".join(chunks)

        snap = campus_nav.build_navigation_brief(schedules, now=clock)
        if snap.current:
            chunks.append(
                f"Current class: {snap.current.subject} ({snap.current.room}) "
                f"until {snap.current.end.strftime('%H:%M')}."
            )
        elif snap.phase == "class_ended" and snap.ended_summary:
            chunks.append(snap.ended_summary)
        elif snap.phase == "day_complete" and snap.ended_summary:
            chunks.append(snap.ended_summary)
        if snap.next_session:
            meta = campus_nav.resolve_room(snap.next_session.room)
            loc_full = campus_nav.describe_room(meta) if meta else snap.next_session.room
            chunks.append(
                f"Next class: {snap.next_session.subject} ({snap.next_session.room}) — "
                f"{snap.next_session.start.strftime('%H:%M')}–{snap.next_session.end.strftime('%H:%M')} · {loc_full}"
            )

            if snap.travel_message:
                chunks.append(snap.travel_message)
            elif snap.travel_seconds is None:
                chunks.append(
                    "Walk estimate unavailable — use official room codes (e.g., TR-05, LT-03) matching `campus_rooms.json`."
                )
            if snap.late_for_next:
                chunks.append("Warning: projected late arrival if you leave immediately.")
            elif snap.urgency_message:
                chunks.append(snap.urgency_message)
            hints = "; ".join(snap.path_hints[:4])
            if hints:
                chunks.append(hints)
        else:
            if snap.current:
                chunks.append("No further parsed sessions listed after your current slot today.")
            elif day_sessions:
                chunks.append(f"No upcoming classes remaining today ({day_label}) — timetable may continue after parsed rows.")
            else:
                chunks.append(
                    "No parsed timetable for today — ensure rows include weekday labels and clocks like `10:30-11:30`."
                )

        return "\n".join(chunks) if chunks else f"{who}, navigation data not available yet."

    if any(word in message_lower for word in schedule_keywords):
        if not schedules:
            return (
                f"{who}, schedules are empty. Ask faculty or admin to publish your weekly timetable "
                "(day, subject, time like 10:00–11:00, room)."
            )

        anchor = _chat_day_anchor(message_lower, datetime.now())
        day_sessions = campus_nav.iter_parsed_sessions_for_day(schedules, anchor)
        if not day_sessions:
            lbl = campus_nav.normalize_weekday(anchor.strftime("%A")) or anchor.strftime("%A")
            return (
                f"No classes parsed for {lbl}. Either it is free or timetable rows "
                'need weekday + time ranges (e.g. Monday, 09:00-10:30, TR-05).'
            )

        lbl = campus_nav.normalize_weekday(anchor.strftime("%A")) or anchor.strftime("%A")
        lines = [
            f"Classes on {lbl} ({anchor.strftime('%d %b')}):",
            campus_nav.briefly_list_sessions(day_sessions),
            "",
            "Tip: arrive ≈10 minutes early; open linked notes beforehand.",
        ]
        return "\n".join(lines)

    if any(word in message_lower for word in notes_keywords):
        if not notes:
            return (
                f"{who}, no notes uploaded yet — ask lecturers to attach PDFs/resources."
            )
        lines = ["Latest notes uploaded on the portal:"]
        for note in notes[:8]:
            path = note["file_path"]
            bullet = (
                f"• {note['subject']} — {note['title']} (open Notes in sidebar)"
                + ("" if path else " (attachment pending)")
            )
            lines.append(bullet)
        lines.append("")
        lines.append(
            "Open the Notes page from the sidebar to browse and download attachments."
        )
        return "\n".join(lines)

    if any(word in message_lower for word in prep_keywords):
        return (
            f"{who}, quick checklist before class:\n"
            "1) Glance timetable + locate block/floor codes (ING / Wolverhampton / HCK).\n"
            "2) Skim lecture notes/objectives.\n"
            "3) List two questions.\n"
            "4) Pack devices + chargers + ID.\n"
            "5) Use the dashboard navigator to gauge walking time early."
        )

    return (
        f"{casual_name}, I'm here for timetables, walking-time estimates between rooms, notes, prep tips "
        "(use the shortcuts below)."
    )


def _rasa_webhook_url() -> str:
    base = os.environ.get("RASA_URL", "http://localhost:5005").rstrip("/")
    return f"{base}/webhooks/rest/webhook"


@app.route("/chat", methods=["POST"])
@csrf.exempt
def chat():
    data = request.get_json() or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"reply": "Please type a message."}), 400

    conn = get_db()
    ai_reply = ai_sara_reply(message, build_sara_context(conn, session))
    if ai_reply:
        return jsonify({"reply": ai_reply, "buttons": _fallback_chat_quick_actions()})

    sender = str(session.get("user_id") or data.get("sender") or "anonymous")
    metadata = {
        "user_id": session.get("user_id"),
        "role": (session.get("role") or "guest"),
        "username": (session.get("username") or "Guest"),
        "page_path": (data.get("page_path") or ""),
        "page_title": (data.get("page_title") or ""),
    }

    try:
        resp = requests.post(
            _rasa_webhook_url(),
            json={"sender": sender, "message": message, "metadata": metadata},
            timeout=12,
        )
        resp.raise_for_status()
        items = resp.json() if resp.content else []
        texts = []
        buttons = []
        for it in items:
            t = (it or {}).get("text")
            if t:
                texts.append(str(t))
            b = (it or {}).get("buttons") or []
            if isinstance(b, list):
                for btn in b:
                    if not isinstance(btn, dict):
                        continue
                    title = (btn.get("title") or "").strip()
                    payload = (btn.get("payload") or "").strip()
                    if title and payload:
                        buttons.append({"title": title, "payload": payload})
        reply = "\n".join(texts).strip()
        forced_legacy_buttons = False
        if _sara_reply_looks_like_stacked_menu(reply):
            reply = _legacy_sara_reply(message)
            forced_legacy_buttons = True
        if not reply:
            reply = "I’m here. Could you rephrase that question?"
        if forced_legacy_buttons:
            uniq_buttons = _fallback_chat_quick_actions()
        else:
            # De-duplicate buttons by title+payload
            seen = set()
            uniq_buttons = []
            for b in buttons:
                key = (b["title"], b["payload"])
                if key in seen:
                    continue
                seen.add(key)
                uniq_buttons.append(b)
        return jsonify({"reply": reply, "buttons": uniq_buttons})
    except Exception:
        # If Rasa is offline/unavailable, keep chat usable.
        return jsonify(
            {"reply": _legacy_sara_reply(message), "buttons": _fallback_chat_quick_actions()},
        )


# Backward-compatible endpoint (old frontend path)
@app.route("/sara_ai", methods=["POST"])
@csrf.exempt
def sara_ai():
    return chat()

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
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        role = request.form.get("role") or ""

        ok_user, username_or_err = validate_username(username)
        if not ok_user:
            flash(username_or_err, "danger")
            return render_template("signup.html")
        username = username_or_err

        ok_pw, pw_or_err = validate_password(password)
        if not ok_pw:
            flash(pw_or_err, "danger")
            return render_template("signup.html")

        if not role in {"student", "teacher"}:
            flash("Please fill in all required fields.", "danger")
            return render_template("signup.html")

        if not EMAIL_PATTERN.match(email):
            flash("Please enter a valid email address.", "danger")
            return render_template("signup.html")

        if database.email_in_use(email):
            flash("This email is already registered.", "danger")
            return render_template("signup.html")

        success, token = database.add_user(username, password, role, email)

        if success:
            verify_url = url_for("verify_email", token=token, _external=True)
            if not email_utils.send_verification_email(email, verify_url):
                session["dev_verify_url"] = verify_url
            return redirect(url_for("verify_email_pending"))
        else:
            flash("Username already exists!", "danger")

    return render_template("signup.html")


@app.route("/verify-email-pending")
def verify_email_pending():
    dev_link = session.pop("dev_verify_url", None)
    return render_template("verify_email_pending.html", dev_verify_url=dev_link)


@app.route("/verify-email/<token>")
def verify_email(token):
    ok, message = database.verify_email_token(token)
    flash(message, "success" if ok else "danger")
    return redirect(url_for("login"))


# =========================
# LOGIN (FIXED)
# =========================
@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        role = (request.form.get("role") or "").lower()

        if email and not EMAIL_PATTERN.match(email):
            flash("Please enter a valid email address.", "danger")
            return render_template("login.html")

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT * FROM users
            WHERE username=? AND LOWER(role)=? AND COALESCE(is_active, 1)=1
            """,
            (username, role),
        )

        user = cursor.fetchone()

        if user and _password_matches(user["password"], password):
            if not (user["password"] or "").startswith("$2"):
                _upgrade_password_hash(user["id"], password)
            stored_email = (user["email"] or "").strip().lower()
            if stored_email:
                if stored_email != email:
                    conn.close()
                    flash("Email does not match this account.", "danger")
                    return render_template("login.html")
                if user["verification_token"] and not user["email_verified"]:
                    conn.close()
                    flash("Please verify your email before logging in. Check your inbox.", "danger")
                    return render_template("login.html")
            elif email:
                conn.close()
                flash("This account has no email on file. Leave email empty or update your profile.", "danger")
                return render_template("login.html")

            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = (user["role"] or role).lower()
            conn.close()
            logger.info(
                "Login success: username=%s role=%s ip=%s",
                username,
                session["role"],
                request.remote_addr,
            )

            if role == "teacher":
                return redirect("/teacher_dashboard")
            elif role == "admin":
                return redirect("/admin_dashboard")
            else:
                return redirect("/student_dashboard")

        conn.close()
        logger.info(
            "Failed login: username=%s role=%s ip=%s",
            username,
            role,
            request.remote_addr,
        )
        flash("Invalid credentials", "danger")

    return render_template("login.html")


@app.route("/reset-password", methods=["POST"])
@limiter.limit("5 per minute")
def reset_password():
    username = (request.form.get("username") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    confirm = request.form.get("confirm_password") or ""

    if not username or not email:
        flash("Username and email are required to reset your password.", "danger")
        return render_template("login.html", show_reset=True)

    if not EMAIL_PATTERN.match(email):
        flash("Please enter a valid email address.", "danger")
        return render_template("login.html", show_reset=True)

    ok_pw, pw_or_err = validate_password(password)
    if not ok_pw:
        flash(pw_or_err, "danger")
        return render_template("login.html", show_reset=True)

    if password != confirm:
        flash("Passwords do not match.", "danger")
        return render_template("login.html", show_reset=True)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, email FROM users
        WHERE username=? AND COALESCE(is_active, 1)=1
        """,
        (username,),
    )
    user = cursor.fetchone()

    if not user:
        conn.close()
        flash("No account found with that username.", "danger")
        return render_template("login.html", show_reset=True)

    stored_email = (user["email"] or "").strip().lower()
    if not stored_email or stored_email != email:
        conn.close()
        flash("Email does not match this account.", "danger")
        return render_template("login.html", show_reset=True)

    hashed_password = bcrypt.generate_password_hash(password).decode("utf-8")
    cursor.execute("UPDATE users SET password=? WHERE id=?", (hashed_password, user["id"]))
    conn.commit()
    conn.close()
    flash("Password updated. You can log in with your new password.", "success")
    return render_template("login.html")


# =========================
# ADMIN LOGIN (PRIVATE)
# =========================
@app.route("/admin")
def admin_entry():
    if session.get("user_id") and (session.get("role") or "").lower() == "admin":
        return redirect(url_for("admin_dashboard"))
    return redirect(url_for("admin_login"))


@app.route("/admin_login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def admin_login():
    if request.method == "POST":
        username = (request.form.get("username", "") or "").strip()
        password = (request.form.get("password", "") or "").strip()

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM users WHERE username=? AND LOWER(role)='admin' AND COALESCE(is_active, 1)=1",
            (username,),
        )
        user = cursor.fetchone()

        if user and _password_matches(user["password"], password):
            if not (user["password"] or "").startswith("$2"):
                _upgrade_password_hash(user["id"], password)
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = (user["role"] or "admin").lower()
            conn.close()
            return redirect("/admin_dashboard")

        conn.close()
        flash("Invalid admin credentials.", "danger")

    return render_template("admin_login.html")


# =========================
# profile
# =========================
@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM subjects ORDER BY name ASC")
    subjects = fetch_subject_names(cursor)

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

        cursor.execute("SELECT role FROM users WHERE id=?", (session["user_id"],))
        user_role = (cursor.fetchone()["role"] or "").lower()
        if user_role == "teacher":
            if not teacher_subject:
                flash("Please select the subject you teach.", "danger")
                conn.close()
                return redirect(url_for("profile"))
            if not subject_is_valid(cursor, teacher_subject):
                flash("Please select a valid subject from the list.", "danger")
                conn.close()
                return redirect(url_for("profile"))

        image_path_to_save = None
        if profile_image and profile_image.filename:
            if not allowed_image(profile_image.filename):
                flash("Invalid image format. Use png, jpg, jpeg, gif, or webp.", "danger")
                conn.close()
                return redirect(url_for("profile"))
            if request.content_length and request.content_length > MAX_UPLOAD_BYTES:
                flash("File too large. Max 5MB.", "danger")
                conn.close()
                return redirect(url_for("profile"))

            cursor.execute("SELECT profile_image FROM users WHERE id=?", (session["user_id"],))
            old_user = cursor.fetchone()
            old_image = old_user["profile_image"] if old_user else None

            filename = f"{uuid.uuid4()}_{secure_filename(profile_image.filename)}"
            upload_folder = os.path.join("uploads", "profile_images")
            os.makedirs(upload_folder, exist_ok=True)
            profile_image.save(os.path.join(upload_folder, filename))
            full_upload_path = os.path.join(upload_folder, filename)
            if not _validate_saved_image_or_cleanup(full_upload_path):
                flash("Invalid file type.", "danger")
                conn.close()
                return redirect(url_for("profile"))
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
# TEACHER NOTES
# =========================
@app.route("/add_note", methods=["POST"])
@role_required("teacher")
def add_note():
    subject = (request.form.get("subject") or "").strip()
    title = request.form.get("title")
    file = request.files.get("file")   # ✅ FIXED

    filename = None

    if not file or file.filename == "":
        flash("Please upload a file for this note.", "danger")
        return redirect(url_for("notes"))

    conn = get_db()
    cursor = conn.cursor()
    if not subject_is_valid(cursor, subject):
        conn.close()
        flash("Please select a valid subject from the list.", "danger")
        return redirect(url_for("notes"))

    filename = str(uuid.uuid4()) + "_" + secure_filename(file.filename)
    upload_folder = "uploads"
    os.makedirs(upload_folder, exist_ok=True)
    file.save(os.path.join(upload_folder, filename))

    cursor.execute("""
        INSERT INTO notes (subject, title, file_path)
        VALUES (?, ?, ?)
    """, (subject, title, filename))   # ✅ SAVE filename

    conn.commit()
    conn.close()

    database.notify_role("student", f"New note uploaded: {title}", "/notes")
    flash("Note uploaded.", "success")
    return redirect(url_for("notes"))


# =========================
# UPLOAD FILES
# =========================

@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory("uploads", filename)


# =========================
# DELETE NOTES
# =========================
@app.route("/delete_note/<int:note_id>", methods=["POST"])
@role_required("teacher")
def delete_note(note_id):

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
@role_required("student")
def student_dashboard():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM schedules ORDER BY id ASC")
    schedules_all = cursor.fetchall()
    conn.close()

    wd = datetime.now().strftime("%A")
    canon_wd = campus_nav.normalize_weekday(wd)
    today_focus = [
        row
        for row in schedules_all
        if campus_nav.normalize_weekday(row["day"]) == canon_wd
    ]
    schedule_preview_rows = today_focus if today_focus else list(schedules_all)[:15]

    nav_brief = campus_nav.build_navigation_brief(schedules_all)
    show_today_banner = bool(today_focus)

    return render_template(
        "dashboard.html",
        schedules=schedule_preview_rows,
        nav_brief=nav_brief,
        schedule_preview_is_today=show_today_banner,
        schedule_preview_total=len(schedules_all),
    )


# =========================
# STUDENTS
# =========================
@app.route("/students")
@role_required("teacher")
def students():
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
@role_required("student", "teacher")
def teachers():
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
@role_required("student", "teacher")
def subjects():

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
@role_required("teacher")
def add_subject():

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
@role_required("student", "teacher")
def notes():

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM notes")
    notes = cursor.fetchall()
    subjects = fetch_subject_names(cursor)
    conn.close()

    return render_template("notes.html", notes=notes, subjects=subjects)


# =========================
# SCHEDULES
# =========================
@app.route("/schedules")
@role_required("student", "teacher")
def schedules():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM schedules ORDER BY id DESC")
    schedules = cursor.fetchall()

    edit_schedule = None
    edit_id = request.args.get("edit_id", type=int)
    if session.get("role") == "teacher" and edit_id:
        cursor.execute("SELECT * FROM schedules WHERE id=?", (edit_id,))
        edit_schedule = cursor.fetchone()

    subjects = fetch_subject_names(cursor)
    conn.close()
    return render_template(
        "schedules.html",
        schedules=schedules,
        edit_schedule=edit_schedule,
        subjects=subjects,
    )


@app.route("/add_schedule", methods=["POST"])
@role_required("teacher")
def add_schedule():

    day = request.form.get("day", "").strip()
    class_name = request.form.get("class_name", "").strip()
    time = request.form.get("time", "").strip()
    room = request.form.get("room", "").strip()

    if not day or not class_name or not time or not room:
        flash("All schedule fields are required.", "danger")
        return redirect(url_for("schedules"))

    conn = get_db()
    cursor = conn.cursor()
    if not subject_is_valid(cursor, class_name):
        conn.close()
        flash("Please select a valid subject from the list.", "danger")
        return redirect(url_for("schedules"))

    cursor.execute(
        "INSERT INTO schedules (day, class_name, time, room) VALUES (?, ?, ?, ?)",
        (day, class_name, time, room),
    )
    conn.commit()
    conn.close()
    flash("Schedule created successfully.", "success")
    return redirect(url_for("schedules"))


@app.route("/update_schedule/<int:schedule_id>", methods=["POST"])
@role_required("teacher")
def update_schedule(schedule_id):

    day = request.form.get("day", "").strip()
    class_name = request.form.get("class_name", "").strip()
    time = request.form.get("time", "").strip()
    room = request.form.get("room", "").strip()

    if not day or not class_name or not time or not room:
        flash("All schedule fields are required.", "danger")
        return redirect(url_for("schedules", edit_id=schedule_id))

    conn = get_db()
    cursor = conn.cursor()
    if not subject_is_valid(cursor, class_name):
        conn.close()
        flash("Please select a valid subject from the list.", "danger")
        return redirect(url_for("schedules", edit_id=schedule_id))

    cursor.execute(
        "UPDATE schedules SET day=?, class_name=?, time=?, room=? WHERE id=?",
        (day, class_name, time, room, schedule_id),
    )
    conn.commit()
    conn.close()
    flash("Schedule updated successfully.", "success")
    return redirect(url_for("schedules"))


@app.route("/delete_schedule/<int:schedule_id>", methods=["POST"])
@role_required("teacher")
def delete_schedule(schedule_id):

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
    conn.commit()
    conn.close()
    flash("Schedule deleted successfully.", "success")
    return redirect(url_for("schedules"))


# =========================
# TEACHER LEAVE
# =========================
@app.route("/teacher_leave")
@role_required("teacher", "student")
def teacher_leave():

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
    subjects = fetch_subject_names(cursor)
    conn.close()

    return render_template(
        "teacher_leave.html",
        teacher_leave=teacher_leave,
        today_absent=today_absent,
        today=today,
        subjects=subjects,
    )


@app.route("/apply_teacher_leave", methods=["POST"])
@role_required("teacher")
def apply_teacher_leave():

    leave_date = request.form.get("date", "").strip()
    subject = request.form.get("subject", "").strip()
    reason = request.form.get("reason", "").strip()

    if not leave_date or not subject or not reason:
        flash("Date, subject, and reason are required.", "danger")
        return redirect(url_for("teacher_leave"))

    conn = get_db()
    cursor = conn.cursor()
    if not subject_is_valid(cursor, subject):
        conn.close()
        flash("Please select a valid subject from the list.", "danger")
        return redirect(url_for("teacher_leave"))

    status = "On Leave" if leave_date == date.today().isoformat() else "Scheduled Leave"

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
@role_required("teacher")
def teacher_dashboard():

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
@role_required("admin")
def admin_dashboard():

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
@role_required("admin")
def admin_users():

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

    page = request.args.get("page", 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page

    sql = "SELECT * FROM users"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([per_page, offset])

    cursor.execute(sql, params)
    users = cursor.fetchall()
    count_sql = "SELECT COUNT(*) AS c FROM users"
    count_params = params[:-2]
    if where:
        count_sql += " WHERE " + " AND ".join(where)
    cursor.execute(count_sql, count_params)
    total = cursor.fetchone()["c"]
    conn.close()

    return render_template(
        "admin_users.html",
        users=users,
        q=q,
        role=role,
        page=page,
        per_page=per_page,
        total=total,
        has_next=(offset + per_page) < total,
    )


@app.route("/admin/users/<int:user_id>/toggle_active", methods=["POST"])
@role_required("admin")
def admin_toggle_user_active(user_id):

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
@role_required("admin")
def admin_update_user_role(user_id):

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
@role_required("admin")
def admin_delete_user(user_id):

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
@role_required("admin")
def admin_subjects():

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM subjects ORDER BY id DESC")
    subjects = cursor.fetchall()
    conn.close()
    return render_template("admin_subjects.html", subjects=subjects)


@app.route("/admin/subjects/add", methods=["POST"])
@role_required("admin")
def admin_add_subject():

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
@role_required("admin")
def admin_delete_subject(subject_row_id):

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM subjects WHERE id=?", (subject_row_id,))
    conn.commit()
    conn.close()
    flash("Subject deleted.", "success")
    return redirect(url_for("admin_subjects"))


@app.route("/admin/notes")
@role_required("admin")
def admin_notes():

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM notes ORDER BY id DESC")
    notes = cursor.fetchall()
    conn.close()
    return render_template("admin_notes.html", notes=notes)


@app.route("/admin/notes/<int:note_id>/delete", methods=["POST"])
@role_required("admin")
def admin_delete_note(note_id):

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
@role_required("admin")
def admin_schedules():

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM schedules ORDER BY id DESC")
    schedules = cursor.fetchall()

    edit_schedule = None
    edit_id = request.args.get("edit_id", type=int)
    if edit_id:
        cursor.execute("SELECT * FROM schedules WHERE id=?", (edit_id,))
        edit_schedule = cursor.fetchone()

    subjects = fetch_subject_names(cursor)
    conn.close()
    return render_template(
        "admin_schedules.html",
        schedules=schedules,
        edit_schedule=edit_schedule,
        subjects=subjects,
    )


@app.route("/admin/schedules/add", methods=["POST"])
@role_required("admin")
def admin_add_schedule():

    day = request.form.get("day", "").strip()
    class_name = request.form.get("class_name", "").strip()
    time = request.form.get("time", "").strip()
    room = request.form.get("room", "").strip()

    if not day or not class_name or not time or not room:
        flash("All schedule fields are required.", "danger")
        return redirect(url_for("admin_schedules"))

    conn = get_db()
    cursor = conn.cursor()
    if not subject_is_valid(cursor, class_name):
        conn.close()
        flash("Please select a valid subject from the list.", "danger")
        return redirect(url_for("admin_schedules"))

    cursor.execute(
        "INSERT INTO schedules (day, class_name, time, room) VALUES (?, ?, ?, ?)",
        (day, class_name, time, room),
    )
    conn.commit()
    conn.close()
    flash("Schedule created.", "success")
    return redirect(url_for("admin_schedules"))


@app.route("/admin/schedules/<int:schedule_id>/update", methods=["POST"])
@role_required("admin")
def admin_update_schedule(schedule_id):

    day = request.form.get("day", "").strip()
    class_name = request.form.get("class_name", "").strip()
    time = request.form.get("time", "").strip()
    room = request.form.get("room", "").strip()

    if not day or not class_name or not time or not room:
        flash("All schedule fields are required.", "danger")
        return redirect(url_for("admin_schedules", edit_id=schedule_id))

    conn = get_db()
    cursor = conn.cursor()
    if not subject_is_valid(cursor, class_name):
        conn.close()
        flash("Please select a valid subject from the list.", "danger")
        return redirect(url_for("admin_schedules", edit_id=schedule_id))

    cursor.execute(
        "UPDATE schedules SET day=?, class_name=?, time=?, room=? WHERE id=?",
        (day, class_name, time, room, schedule_id),
    )
    conn.commit()
    conn.close()
    flash("Schedule updated.", "success")
    return redirect(url_for("admin_schedules"))


@app.route("/admin/schedules/<int:schedule_id>/delete", methods=["POST"])
@role_required("admin")
def admin_delete_schedule(schedule_id):

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
    conn.commit()
    conn.close()
    flash("Schedule deleted.", "success")
    return redirect(url_for("admin_schedules"))


@app.route("/admin/leaves")
@role_required("admin")
def admin_leaves():

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
@role_required("admin")
def admin_update_leave_status(leave_id):

    status = (request.form.get("status") or "").strip()
    if status not in {"Approved", "Rejected", "Pending", "On Leave", "Scheduled Leave"}:
        flash("Invalid status.", "danger")
        return redirect(url_for("admin_leaves"))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT teacher_id FROM teacher_leave WHERE id=?", (leave_id,))
    leave_row = cursor.fetchone()
    cursor.execute("UPDATE teacher_leave SET status=? WHERE id=?", (status, leave_id))
    conn.commit()
    if leave_row:
        database.create_notification(
            leave_row["teacher_id"],
            f"Leave request {status}",
            "/teacher_leave",
        )
    conn.close()
    flash("Leave status updated.", "success")
    return redirect(url_for("admin_leaves"))


@app.route("/admin/reports")
@role_required("admin")
def admin_reports():
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
@role_required("admin")
def admin_export_users():

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
@role_required("admin")
def admin_export_subjects():

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, code, subject_id FROM subjects ORDER BY id ASC")
    subjects = cursor.fetchall()
    conn.close()

    rows = [[str(s["id"]), s["name"] or "", s["code"] or "", s["subject_id"] or ""] for s in subjects]
    return _csv_response("subjects.csv", ["id", "name", "code", "subject_id"], rows)


@app.route("/admin/backup")
@role_required("admin")
def admin_backup():

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
@role_required("admin")
def init_db():
    database.create_tables()
    return "Database created!"


@app.route("/robots.txt")
def robots():
    return Response("User-agent: *\nDisallow: /admin\nDisallow: /api/", mimetype="text/plain")


@app.route("/health")
def health():
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})


@app.route("/schedules/export/pdf")
@role_required("student", "teacher", "admin")
def export_schedule_pdf():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT day, class_name, time, room FROM schedules ORDER BY day, time")
    rows = cursor.fetchall()
    conn.close()

    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(50, 750, "Smart Campus — Timetable")
        c.setFont("Helvetica", 11)
        y = 720
        for r in rows:
            line = f"{r['day']}: {r['class_name']} | {r['time']} | {r['room']}"
            c.drawString(50, y, line)
            y -= 18
            if y < 50:
                c.showPage()
                y = 750
        c.save()
        buf.seek(0)
        return Response(buf.read(), mimetype="application/pdf", headers={
            "Content-Disposition": "attachment; filename=schedule.pdf"
        })
    except ImportError:
        flash("PDF export unavailable. Install reportlab.", "danger")
        return redirect(url_for("schedules"))


@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


@app.errorhandler(500)
def server_error(e):
    return render_template("500.html"), 500


database.create_tables()

if __name__ == "__main__":
    app.run(debug=True)
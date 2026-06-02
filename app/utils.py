import re
from functools import wraps

from flask import flash, g, redirect, session, url_for
from flask_bcrypt import Bcrypt

bcrypt = Bcrypt()

EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def init_bcrypt(app):
    bcrypt.init_app(app)


def get_db():
    import sqlite3

    if "db" not in g:
        g.db = sqlite3.connect("database.db")
        g.db.row_factory = sqlite3.Row
    return g.db


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


def require_role(*roles):
    if "user_id" not in session:
        return False
    current = (session.get("role") or "").lower()
    return current in {r.lower() for r in roles}


def password_matches(stored: str, plain: str) -> bool:
    if not stored or not plain:
        return False
    if isinstance(stored, str) and stored.startswith("$2"):
        return bcrypt.check_password_hash(stored, plain)
    return stored == plain


def upgrade_password_hash(user_id: int, plain: str) -> None:
    hashed = bcrypt.generate_password_hash(plain).decode("utf-8")
    conn = get_db()
    conn.execute("UPDATE users SET password=? WHERE id=?", (hashed, user_id))
    conn.commit()


def allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def validate_saved_image_or_cleanup(saved_path: str) -> bool:
    import os

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


def fetch_subject_names(cursor):
    cursor.execute(
        """
        SELECT name FROM subjects
        WHERE name IS NOT NULL AND TRIM(name) != ''
        ORDER BY name ASC
        """
    )
    return [row["name"] for row in cursor.fetchall()]


def subject_is_valid(cursor, subject_name):
    if not subject_name:
        return False
    return subject_name in fetch_subject_names(cursor)

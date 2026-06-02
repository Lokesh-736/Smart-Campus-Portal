import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from flask_bcrypt import generate_password_hash


def connect():
    return sqlite3.connect("database.db")


def create_tables():
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("PRAGMA table_info(users)")
    existing_columns = {row[1] for row in cursor.fetchall()}
    extra_columns = {
        "full_name": "TEXT",
        "email": "TEXT",
        "phone": "TEXT",
        "bio": "TEXT",
        "profile_image": "TEXT",
        "teacher_subject": "TEXT",
        "is_active": "INTEGER DEFAULT 1",
        "email_verified": "INTEGER DEFAULT 0",
        "verification_token": "TEXT",
        "verification_token_expires": "TEXT",
    }
    for column_name, column_type in extra_columns.items():
        if column_name not in existing_columns:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {column_name} {column_type}")

    cursor.execute(
        "UPDATE users SET email_verified = 1 WHERE verification_token IS NULL"
    )

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject TEXT NOT NULL,
        title TEXT NOT NULL,
        file_path TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day TEXT,
        class_name TEXT,
        time TEXT,
        room TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS teacher_leave (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        teacher_id INTEGER,
        date TEXT,
        subject TEXT,
        reason TEXT,
        status TEXT DEFAULT 'Pending',
        FOREIGN KEY (teacher_id) REFERENCES users(id)
    )
    """)

    cursor.execute("PRAGMA table_info(teacher_leave)")
    leave_columns = {row[1] for row in cursor.fetchall()}
    if "subject" not in leave_columns:
        cursor.execute("ALTER TABLE teacher_leave ADD COLUMN subject TEXT")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS student_hobbies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER,
        hobby TEXT,
        FOREIGN KEY (student_id) REFERENCES users(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS subjects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        code TEXT,
        subject_id TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS announcements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        target_role TEXT DEFAULT 'all',
        created_by INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (created_by) REFERENCES users(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER,
        schedule_id INTEGER,
        date TEXT,
        status TEXT DEFAULT 'Present',
        marked_by INTEGER,
        FOREIGN KEY (student_id) REFERENCES users(id),
        FOREIGN KEY (schedule_id) REFERENCES schedules(id),
        FOREIGN KEY (marked_by) REFERENCES users(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS assignments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        description TEXT,
        subject TEXT,
        due_date TEXT,
        created_by INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (created_by) REFERENCES users(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        assignment_id INTEGER,
        student_id INTEGER,
        file_path TEXT,
        submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        grade TEXT,
        feedback TEXT,
        FOREIGN KEY (assignment_id) REFERENCES assignments(id),
        FOREIGN KEY (student_id) REFERENCES users(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_id INTEGER,
        receiver_id INTEGER,
        subject TEXT,
        body TEXT,
        is_read INTEGER DEFAULT 0,
        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (sender_id) REFERENCES users(id),
        FOREIGN KEY (receiver_id) REFERENCES users(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        description TEXT,
        event_date TEXT,
        event_type TEXT,
        created_by INTEGER,
        FOREIGN KEY (created_by) REFERENCES users(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        message TEXT,
        link TEXT,
        is_read INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS grades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER,
        subject TEXT,
        assignment_name TEXT,
        marks_obtained REAL,
        total_marks REAL,
        grade_letter TEXT,
        recorded_by INTEGER,
        recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (student_id) REFERENCES users(id),
        FOREIGN KEY (recorded_by) REFERENCES users(id)
    )
    """)

    cursor.execute("SELECT id FROM users WHERE username=?", ("Lokesh Thapa",))
    row = cursor.fetchone()
    if not row:
        cursor.execute(
            "INSERT INTO users (username, password, role, is_active) VALUES (?, ?, ?, 1)",
            ("Lokesh Thapa", generate_password_hash("Admin123").decode("utf-8"), "admin"),
        )
    else:
        cursor.execute(
            "UPDATE users SET role='admin', is_active=1 WHERE id=? AND LOWER(role)='admin'",
            (row[0],),
        )

    cursor.execute(
        "UPDATE users SET is_active=1 WHERE LOWER(role)='admin' AND COALESCE(is_active, 0)=0"
    )

    _upgrade_plaintext_passwords(cursor)
    conn.commit()
    conn.close()


def _upgrade_plaintext_passwords(cursor):
    cursor.execute(
        "SELECT id, password FROM users WHERE password IS NOT NULL AND password NOT LIKE '$2%'"
    )
    for user_id, password in cursor.fetchall():
        hashed = generate_password_hash(password).decode("utf-8")
        cursor.execute("UPDATE users SET password=? WHERE id=?", (hashed, user_id))


def email_in_use(email):
    if not email:
        return False
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM users WHERE LOWER(TRIM(email)) = LOWER(TRIM(?))",
        (email,),
    )
    row = cursor.fetchone()
    conn.close()
    return row is not None


def add_user(username, password, role, email=None):
    conn = connect()
    cursor = conn.cursor()
    token = str(uuid.uuid4())
    expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    hashed_password = generate_password_hash(password).decode("utf-8")

    try:
        cursor.execute(
            """
            INSERT INTO users (
                username, password, role, email,
                email_verified, verification_token, verification_token_expires
            )
            VALUES (?, ?, ?, ?, 0, ?, ?)
            """,
            (username, hashed_password, role, email, token, expires),
        )
        conn.commit()
        return True, token
    except Exception as e:
        print("Error:", e)
        return False, None
    finally:
        conn.close()


def verify_email_token(token):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, verification_token_expires, email_verified
        FROM users WHERE verification_token = ?
        """,
        (token,),
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False, "Invalid or expired verification link."

    if row[2]:
        conn.close()
        return True, "Email is already verified."

    expires_raw = row[1]
    if expires_raw:
        try:
            expires = datetime.fromisoformat(expires_raw)
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expires:
                conn.close()
                return False, "Verification link has expired. Please sign up again or contact support."
        except ValueError:
            pass

    cursor.execute(
        """
        UPDATE users
        SET email_verified = 1,
            verification_token = NULL,
            verification_token_expires = NULL
        WHERE id = ?
        """,
        (row[0],),
    )
    conn.commit()
    conn.close()
    return True, "Email verified successfully."


def create_notification(user_id, message, link=None):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO notifications (user_id, message, link) VALUES (?, ?, ?)",
        (user_id, message, link),
    )
    conn.commit()
    conn.close()


def notify_role(role, message, link=None):
    conn = connect()
    cursor = conn.cursor()
    if role == "all":
        cursor.execute("SELECT id FROM users WHERE COALESCE(is_active, 1)=1")
    else:
        cursor.execute(
            "SELECT id FROM users WHERE LOWER(role)=? AND COALESCE(is_active, 1)=1",
            (role.lower(),),
        )
    user_ids = [r[0] for r in cursor.fetchall()]
    for uid in user_ids:
        cursor.execute(
            "INSERT INTO notifications (user_id, message, link) VALUES (?, ?, ?)",
            (uid, message, link),
        )
    conn.commit()
    conn.close()


def unread_notification_count(user_id):
    if not user_id:
        return 0
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0",
        (user_id,),
    )
    count = cursor.fetchone()[0]
    conn.close()
    return count


def unread_message_count(user_id):
    if not user_id:
        return 0
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM messages WHERE receiver_id=? AND is_read=0",
        (user_id,),
    )
    count = cursor.fetchone()[0]
    conn.close()
    return count


def get_announcements_for_role(role, limit=5):
    conn = connect()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    role_l = (role or "").lower()
    cursor.execute(
        """
        SELECT a.*, u.username AS author_name
        FROM announcements a
        LEFT JOIN users u ON u.id = a.created_by
        WHERE target_role='all' OR LOWER(target_role)=?
        ORDER BY a.created_at DESC
        LIMIT ?
        """,
        (role_l, limit),
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def create_database():
    create_tables()

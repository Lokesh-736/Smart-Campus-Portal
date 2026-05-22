import sqlite3
import uuid
from datetime import datetime, timedelta

def connect():
    return sqlite3.connect("database.db")


def create_tables():
    conn = connect()
    cursor = conn.cursor()

    # USERS TABLE
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Backward-compatible profile fields for existing databases
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

    # Existing accounts (no pending token) are treated as already verified
    cursor.execute(
        "UPDATE users SET email_verified = 1 WHERE verification_token IS NULL"
    )

    # NOTES TABLE
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject TEXT NOT NULL,
        title TEXT NOT NULL,
        file_path TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # SCHEDULES TABLE
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day TEXT,
        class_name TEXT,
        time TEXT,
        room TEXT
)
""")
    
    #TEACHER LEAVE TABLE
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

    # Backward-compatible migration for existing teacher_leave table
    cursor.execute("PRAGMA table_info(teacher_leave)")
    leave_columns = {row[1] for row in cursor.fetchall()}
    if "subject" not in leave_columns:
        cursor.execute("ALTER TABLE teacher_leave ADD COLUMN subject TEXT")
    
    # STUDENT HOBBIES TABLE
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS student_hobbies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER,
        hobby TEXT,
        FOREIGN KEY (student_id) REFERENCES users(id)
)
""")
    
    # SUBJECTS TABLE
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    code TEXT,
    subject_id TEXT
)
""")

    # Ensure a default admin account exists (and has expected credentials)
    cursor.execute("SELECT id FROM users WHERE username=?", ("Lokesh Thapa",))
    row = cursor.fetchone()
    if not row:
        cursor.execute(
            "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
            ("Lokesh Thapa", "Admin123", "admin"),
        )
    else:
        cursor.execute(
            "UPDATE users SET password=?, role=? WHERE id=?",
            ("Admin123", "admin", row[0]),
        )

    conn.commit()
    conn.close()


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
    expires = (datetime.utcnow() + timedelta(hours=24)).isoformat()

    try:
        cursor.execute(
            """
            INSERT INTO users (
                username, password, role, email,
                email_verified, verification_token, verification_token_expires
            )
            VALUES (?, ?, ?, ?, 0, ?, ?)
            """,
            (username, password, role, email, token, expires),
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
            if datetime.utcnow() > expires:
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


def create_database():
    create_tables()
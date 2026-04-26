import sqlite3

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
    }
    for column_name, column_type in extra_columns.items():
        if column_name not in existing_columns:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {column_name} {column_type}")

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


def add_user(username, password, role):
    conn = connect()
    cursor = conn.cursor()

    try:
        cursor.execute("""
        INSERT INTO users (username, password, role)
        VALUES (?, ?, ?)
        """, (username, password, role))

        conn.commit()
        return True

    except Exception as e:
        print("Error:", e)
        return False

    finally:
        conn.close()


def create_database():
    create_tables()
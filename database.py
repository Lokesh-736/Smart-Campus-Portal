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
        reason TEXT,
        status TEXT DEFAULT 'Pending',
        FOREIGN KEY (teacher_id) REFERENCES users(id)
)
""")
    
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
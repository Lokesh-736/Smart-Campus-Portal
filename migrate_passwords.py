import sqlite3
from flask_bcrypt import generate_password_hash


def looks_like_bcrypt(password: str) -> bool:
    return isinstance(password, str) and password.startswith("$2")


def migrate():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(users)")
    columns = {row[1] for row in cursor.fetchall()}
    has_is_active = "is_active" in columns
    cursor.execute("SELECT id, password, role FROM users")
    users = cursor.fetchall()
    migrated = 0

    for user_id, password, role in users:
        if not password or looks_like_bcrypt(password):
            continue
        hashed = generate_password_hash(password).decode("utf-8")
        cursor.execute("UPDATE users SET password=? WHERE id=?", (hashed, user_id))
        migrated += 1

    cursor.execute(
        "UPDATE users SET is_active=1 WHERE LOWER(role)='admin' AND COALESCE(is_active, 0)=0"
    )
    conn.commit()
    conn.close()
    print(f"Migrated {migrated} plaintext password(s).")


if __name__ == "__main__":
    migrate()

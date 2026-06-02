import re

USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9 ]{3,50}$")
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PASSWORD_PATTERN = re.compile(r"^(?=.*\d).{8,}$")


def validate_username(username: str) -> tuple[bool, str]:
    username = (username or "").strip()
    if not USERNAME_PATTERN.match(username):
        return False, "Username must be 3–50 characters (letters, numbers, spaces)."
    return True, username


def validate_email(email: str, required: bool = False) -> tuple[bool, str]:
    email = (email or "").strip().lower()
    if not email:
        if required:
            return False, "Email is required."
        return True, email
    if not EMAIL_PATTERN.match(email):
        return False, "Please enter a valid email address."
    return True, email


def validate_password(password: str) -> tuple[bool, str]:
    if not password or not PASSWORD_PATTERN.match(password):
        return False, "Password must be at least 8 characters and include a number."
    return True, password


def validate_upload_size(content_length: int | None, max_bytes: int) -> bool:
    return not content_length or content_length <= max_bytes

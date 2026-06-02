"""Smart Campus Portal application package.

Routes are organized into blueprints under app/features and app/chat.
Core auth, student, teacher, and admin routes remain in root app.py
for stability; migrate incrementally to blueprints as needed.
"""

from flask import Flask

from app.extensions import csrf, limiter, bcrypt
from app.features.routes import features_bp
from app.logging_config import setup_logging
from app.utils import close_db, init_bcrypt


def create_app():
    setup_logging()
    application = Flask(__name__)
    application.secret_key = __import__("os").environ.get("SECRET_KEY", "change-me-in-production")
    application.config["WTF_CSRF_ENABLED"] = True
    application.config["SESSION_COOKIE_HTTPONLY"] = True
    application.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    application.config["SESSION_COOKIE_SECURE"] = (
        __import__("os").environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"
    )
    from datetime import timedelta
    application.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)

    init_bcrypt(application)
    bcrypt.init_app(application)
    csrf.init_app(application)
    limiter.init_app(application)
    application.register_blueprint(features_bp)
    application.teardown_appcontext(close_db)
    return application

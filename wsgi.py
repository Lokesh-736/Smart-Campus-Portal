"""Gunicorn entry point.

Render runs ``gunicorn app:app``, but the ``app/`` package shadows ``app.py``.
Use: gunicorn wsgi:app
"""
import importlib.util
from pathlib import Path

_root = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("smart_campus_app", _root / "app.py")
_module = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_module)

app = _module.app

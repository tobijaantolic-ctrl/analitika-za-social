import os
import secrets
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent

# Load .env for local development (no-op if file missing or dotenv unavailable).
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_DIR / ".env")
except ImportError:  # pragma: no cover — defensive
    pass


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


FLASK_ENV = os.environ.get("FLASK_ENV", "development").lower()
IS_PRODUCTION = FLASK_ENV == "production"

SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
PORT = env_int("PORT", 5455)
HOST = os.environ.get("HOST", "127.0.0.1")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "")

UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", PROJECT_DIR / "uploads")).expanduser()
TIKTOK_UPLOAD_DIR = Path(
    os.environ.get("TIKTOK_UPLOAD_DIR", UPLOAD_DIR / "tiktok")
).expanduser()
MAX_UPLOAD_MB = env_int("MAX_UPLOAD_MB", 600)

TRANSCRIPTION_ENABLED = env_bool("TRANSCRIPTION_ENABLED", not IS_PRODUCTION)
TRANSCRIPTION_PYTHON = Path(
    os.environ.get("TRANSCRIPTION_PYTHON", sys.executable)
).expanduser()
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "turbo")
WHISPER_LANGUAGE = os.environ.get("WHISPER_LANGUAGE", "sl")

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH")
AUTH_REQUIRED = env_bool(
    "AUTH_REQUIRED",
    bool(ADMIN_PASSWORD or ADMIN_PASSWORD_HASH) or IS_PRODUCTION,
)

OPENCLAW_URL = os.environ.get("OPENCLAW_URL", "ws://localhost:18789")
OPENCLAW_TOKEN = os.environ.get("OPENCLAW_TOKEN", "")
ENABLE_OPENCLAW = env_bool("ENABLE_OPENCLAW", not IS_PRODUCTION)

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")

FB_COOKIES_FILE = Path(
    os.environ.get("FB_COOKIES_FILE", PROJECT_DIR / "cookies" / "facebook.txt")
).expanduser()
FB_COOKIES_EXPIRY_WARNING_DAYS = env_int("FB_COOKIES_EXPIRY_WARNING_DAYS", 25)

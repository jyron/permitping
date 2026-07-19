import os

from dotenv import load_dotenv

load_dotenv()

APP_NAME = os.getenv("APP_NAME", "PermitPing")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./permitping.db")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "alerts@permitping.com")

CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "86400"))
LOOKUP_CACHE_SECONDS = int(os.getenv("LOOKUP_CACHE_SECONDS", "300"))
LOGIN_TOKEN_DAYS = int(os.getenv("LOGIN_TOKEN_DAYS", "30"))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

# Free Socrata developer app token (evergreen.data.socrata.com) — without it,
# Socrata portals aggressively throttle/ban datacenter IPs
SOCRATA_APP_TOKEN = os.getenv("SOCRATA_APP_TOKEN", "")

ACCELA_APP_ID = os.getenv("ACCELA_APP_ID", "")
ACCELA_APP_SECRET = os.getenv("ACCELA_APP_SECRET", "")

PLAN_LIMITS = {
    "free": int(os.getenv("FREE_PLAN_MONITOR_LIMIT", "3")),
    "standard": int(os.getenv("STANDARD_PLAN_MONITOR_LIMIT", "25")),
    "unlimited": None,
}

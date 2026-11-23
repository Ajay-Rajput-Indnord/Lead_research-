"""Central configuration: API keys and tunables are read from .env."""
import os

from dotenv import load_dotenv

load_dotenv()

EXA_API_KEY = os.getenv("EXA_API_KEY", "")
PDL_API_KEY = os.getenv("PDL_API_KEY", "")
# ANTHROPIC_API_KEY is picked up automatically by the anthropic SDK.

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
DB_PATH = os.getenv("DB_PATH", "lead_research.db")

# People Data Labs is optional. Off by default: people come from the LinkedIn extension instead.
USE_PDL = os.getenv("USE_PDL", "false").lower() in ("1", "true", "yes")
PDL_MAX_PEOPLE = int(os.getenv("PDL_MAX_PEOPLE", "10"))

# Local API the Chrome extension talks to.
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8765"))

# Who the emails come from (used by the email agent's prompt + signature).
SENDER_NAME = os.getenv("SENDER_NAME", "")
SENDER_TITLE = os.getenv("SENDER_TITLE", "")
SENDER_COMPANY = os.getenv("SENDER_COMPANY", "")
SENDER_OFFERING = os.getenv("SENDER_OFFERING", "")  # one or two lines on what you sell

# SMTP settings for sending email. If SMTP_HOST is empty, emails are only saved as drafts.
#   Gmail:          smtp.gmail.com      587 starttls (use an App Password)
#   Microsoft 365:  smtp.office365.com  587 starttls
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT") or "587")
SMTP_SECURITY = (os.getenv("SMTP_SECURITY") or ("ssl" if SMTP_PORT == 465 else "starttls")).lower()  # starttls | ssl | none
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM") or SMTP_USER  # blank in .env -> use the login address


def require(name: str, value: str) -> str:
    if not value:
        raise SystemExit(f"Missing {name}. Add it to your .env file (see .env.example).")
    return value

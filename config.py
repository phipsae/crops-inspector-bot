import os

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
DATABASE_URL = os.environ["DATABASE_URL"]
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5")

RATE_LIMIT_PER_USER_PER_DAY = int(os.environ.get("RATE_LIMIT_PER_USER_PER_DAY", "3"))
RATE_LIMIT_GLOBAL_PER_DAY = int(os.environ.get("RATE_LIMIT_GLOBAL_PER_DAY", "30"))
RATE_LIMIT_USER_COOLDOWN_SECONDS = int(
    os.environ.get("RATE_LIMIT_USER_COOLDOWN_SECONDS", "60")
)

_owner_ids = os.environ.get("OWNER_TELEGRAM_IDS", "").strip()
OWNER_TELEGRAM_IDS: set[int] = (
    {int(x) for x in _owner_ids.split(",") if x.strip()} if _owner_ids else set()
)

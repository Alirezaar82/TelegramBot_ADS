from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int = 0) -> int:
    value = _env(name)
    if not value:
        return default
    return int(value)


def _env_int_list(name: str) -> list[int]:
    raw = _env(name)
    if not raw:
        return []
    items = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            items.append(int(part))
    return items


def _env_list(name: str) -> list[str]:
    raw = _env(name)
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


token = _env("BOT_TOKEN")

# auto = MySQL if available, otherwise SQLite (best for small containers)
# mysql = force MySQL/MariaDB
# sqlite = force SQLite file DB (no server install)
db_engine = (_env("DB_ENGINE", "auto") or "auto").lower()
sqlite_path = _env("SQLITE_PATH", "data/bot.db")

db = {
    "host": _env("DB_HOST", "localhost"),
    "user": _env("DB_USER", "root"),
    "password": _env("DB_PASSWORD", ""),
    "database": _env("DB_NAME", "Ads"),
}
if _env("DB_PORT"):
    db["port"] = _env_int("DB_PORT", 3306)
if _env("DB_SOCKET"):
    db["unix_socket"] = _env("DB_SOCKET")

admin_id = _env_int("ADMIN_ID", 1297080099)

links = _env_list("CHANNEL_LINKS")
channels = _env_int_list("CHANNELS")

ad_price = _env_int("AD_PRICE", 15000)
referral_bonus = _env_int("REFERRAL_BONUS", 1000)

bot_username = _env("BOT_USERNAME", "Ads_for_channelbot")
referral_image = _env("REFERRAL_IMAGE", "Referrals header - sm.webp")

"""Database access helpers (MySQL or SQLite)."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

import config

logger = logging.getLogger(__name__)

_ENGINE = "mysql"  # set by db_setup.setup_database()


def set_engine(engine: str) -> None:
    global _ENGINE
    if engine not in ("mysql", "sqlite"):
        raise ValueError(f"unsupported engine: {engine}")
    _ENGINE = engine
    logger.info("database engine: %s", engine)


def get_engine() -> str:
    return _ENGINE


def using_sqlite() -> bool:
    return _ENGINE == "sqlite"


def _ph(sql: str) -> str:
    """Convert MySQL-style %s placeholders to SQLite ? when needed."""
    if _ENGINE == "sqlite":
        return sql.replace("%s", "?")
    return sql


@contextmanager
def get_connection(database: Optional[str] = None) -> Iterator:
    if _ENGINE == "sqlite":
        path = Path(config.sqlite_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return

    import mysql.connector

    kwargs = {
        "host": config.db["host"],
        "user": config.db["user"],
        "password": config.db.get("password") or "",
    }
    if config.db.get("port"):
        kwargs["port"] = config.db["port"]
    if config.db.get("unix_socket"):
        kwargs["unix_socket"] = config.db["unix_socket"]
    if database is not None:
        kwargs["database"] = database
    elif "database" in config.db:
        kwargs["database"] = config.db["database"]

    connection = mysql.connector.connect(**kwargs)
    connection.autocommit = False
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def ensure_sqlite_schema() -> None:
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                balance INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS support_messages (
                user_id INTEGER PRIMARY KEY,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS charge_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'denied')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_charge_user ON charge_requests(user_id);
            CREATE INDEX IF NOT EXISTS idx_charge_status ON charge_requests(status);
            """
        )


def ensure_user(user_id: int) -> bool:
    """Insert user if missing. Returns True when a new row was created."""
    with get_connection() as connection:
        cursor = connection.cursor()
        if using_sqlite():
            cursor.execute(
                "INSERT OR IGNORE INTO users (id, balance) VALUES (?, 0)",
                (user_id,),
            )
        else:
            cursor.execute(
                "INSERT IGNORE INTO users (id, balance) VALUES (%s, 0)",
                (user_id,),
            )
        return cursor.rowcount == 1


def user_exists(user_id: int) -> bool:
    with get_connection() as connection:
        cursor = connection.cursor()
        cursor.execute(_ph("SELECT 1 FROM users WHERE id = %s LIMIT 1"), (user_id,))
        return cursor.fetchone() is not None


def get_balance(user_id: int) -> Optional[int]:
    with get_connection() as connection:
        cursor = connection.cursor()
        cursor.execute(_ph("SELECT balance FROM users WHERE id = %s"), (user_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        return int(row[0])


def add_balance(user_id: int, amount: int) -> Optional[int]:
    if amount <= 0:
        raise ValueError("amount must be positive")
    with get_connection() as connection:
        cursor = connection.cursor()
        cursor.execute(
            _ph("UPDATE users SET balance = balance + %s WHERE id = %s"),
            (amount, user_id),
        )
        if cursor.rowcount == 0:
            return None
        cursor.execute(_ph("SELECT balance FROM users WHERE id = %s"), (user_id,))
        row = cursor.fetchone()
        return None if row is None else int(row[0])


def deduct_balance(user_id: int, amount: int) -> Optional[int]:
    """Atomically deduct amount when balance is sufficient. Returns new balance or None."""
    if amount <= 0:
        raise ValueError("amount must be positive")
    with get_connection() as connection:
        cursor = connection.cursor()
        cursor.execute(
            _ph(
                "UPDATE users SET balance = balance - %s "
                "WHERE id = %s AND balance >= %s"
            ),
            (amount, user_id, amount),
        )
        if cursor.rowcount == 0:
            return None
        cursor.execute(_ph("SELECT balance FROM users WHERE id = %s"), (user_id,))
        row = cursor.fetchone()
        return None if row is None else int(row[0])


def save_support_message(user_id: int, message: str) -> None:
    with get_connection() as connection:
        cursor = connection.cursor()
        if using_sqlite():
            cursor.execute(
                """
                INSERT INTO support_messages (user_id, message)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    message = excluded.message,
                    created_at = CURRENT_TIMESTAMP
                """,
                (user_id, message),
            )
        else:
            cursor.execute(
                """
                INSERT INTO support_messages (user_id, message)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE
                    message = VALUES(message),
                    created_at = CURRENT_TIMESTAMP
                """,
                (user_id, message),
            )


def get_support_message(user_id: int) -> Optional[str]:
    with get_connection() as connection:
        cursor = connection.cursor()
        cursor.execute(
            _ph("SELECT message FROM support_messages WHERE user_id = %s"),
            (user_id,),
        )
        row = cursor.fetchone()
        return None if row is None else row[0]


def delete_support_message(user_id: int) -> None:
    with get_connection() as connection:
        cursor = connection.cursor()
        cursor.execute(
            _ph("DELETE FROM support_messages WHERE user_id = %s"),
            (user_id,),
        )


def create_charge_request(user_id: int, amount: int) -> int:
    with get_connection() as connection:
        cursor = connection.cursor()
        cursor.execute(
            _ph(
                "INSERT INTO charge_requests (user_id, amount, status) "
                "VALUES (%s, %s, 'pending')"
            ),
            (user_id, amount),
        )
        return int(cursor.lastrowid)


def get_charge_request(request_id: int) -> Optional[tuple]:
    with get_connection() as connection:
        cursor = connection.cursor()
        cursor.execute(
            _ph(
                "SELECT id, user_id, amount, status FROM charge_requests WHERE id = %s"
            ),
            (request_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return (int(row[0]), int(row[1]), int(row[2]), row[3])


def finalize_charge_request(request_id: int, status: str) -> Optional[tuple]:
    """Mark request approved/denied once. Returns (user_id, amount) on success."""
    if status not in ("approved", "denied"):
        raise ValueError("invalid status")

    with get_connection() as connection:
        cursor = connection.cursor()
        select_sql = (
            "SELECT id, user_id, amount, status FROM charge_requests WHERE id = %s"
        )
        if not using_sqlite():
            select_sql += " FOR UPDATE"
        cursor.execute(_ph(select_sql), (request_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        _, user_id, amount, current = row
        if current != "pending":
            return None

        cursor.execute(
            _ph(
                "UPDATE charge_requests SET status = %s "
                "WHERE id = %s AND status = 'pending'"
            ),
            (status, request_id),
        )
        if cursor.rowcount == 0:
            return None

        if status == "approved":
            cursor.execute(
                _ph("UPDATE users SET balance = balance + %s WHERE id = %s"),
                (amount, user_id),
            )
        return (int(user_id), int(amount))


def is_duplicate_error(exc: Exception) -> bool:
    if using_sqlite():
        return isinstance(exc, sqlite3.IntegrityError)
    from mysql.connector import Error

    return isinstance(exc, Error) and getattr(exc, "errno", None) == 1062

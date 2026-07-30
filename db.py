"""Database access helpers with parameterized queries."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator, Optional

import mysql.connector
from mysql.connector import Error

import config

logger = logging.getLogger(__name__)


@contextmanager
def get_connection(database: Optional[str] = None) -> Iterator:
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


def ensure_user(user_id: int) -> bool:
    """Insert user if missing. Returns True when a new row was created."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT IGNORE INTO users (id, balance) VALUES (%s, 0)",
                (user_id,),
            )
            return cursor.rowcount == 1


def user_exists(user_id: int) -> bool:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM users WHERE id = %s LIMIT 1", (user_id,))
            return cursor.fetchone() is not None


def get_balance(user_id: int) -> Optional[int]:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT balance FROM users WHERE id = %s", (user_id,))
            row = cursor.fetchone()
            return None if row is None else int(row[0])


def add_balance(user_id: int, amount: int) -> Optional[int]:
    if amount <= 0:
        raise ValueError("amount must be positive")
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE users SET balance = balance + %s WHERE id = %s",
                (amount, user_id),
            )
            if cursor.rowcount == 0:
                return None
            cursor.execute("SELECT balance FROM users WHERE id = %s", (user_id,))
            row = cursor.fetchone()
            return None if row is None else int(row[0])


def deduct_balance(user_id: int, amount: int) -> Optional[int]:
    """Atomically deduct amount when balance is sufficient. Returns new balance or None."""
    if amount <= 0:
        raise ValueError("amount must be positive")
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE users SET balance = balance - %s "
                "WHERE id = %s AND balance >= %s",
                (amount, user_id, amount),
            )
            if cursor.rowcount == 0:
                return None
            cursor.execute("SELECT balance FROM users WHERE id = %s", (user_id,))
            row = cursor.fetchone()
            return None if row is None else int(row[0])


def save_support_message(user_id: int, message: str) -> None:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO support_messages (user_id, message)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE message = VALUES(message), created_at = CURRENT_TIMESTAMP
                """,
                (user_id, message),
            )


def get_support_message(user_id: int) -> Optional[str]:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT message FROM support_messages WHERE user_id = %s",
                (user_id,),
            )
            row = cursor.fetchone()
            return None if row is None else row[0]


def delete_support_message(user_id: int) -> None:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM support_messages WHERE user_id = %s",
                (user_id,),
            )


def create_charge_request(user_id: int, amount: int) -> int:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO charge_requests (user_id, amount, status) VALUES (%s, %s, 'pending')",
                (user_id, amount),
            )
            return int(cursor.lastrowid)


def get_charge_request(request_id: int) -> Optional[tuple]:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, user_id, amount, status FROM charge_requests WHERE id = %s",
                (request_id,),
            )
            return cursor.fetchone()


def finalize_charge_request(request_id: int, status: str) -> Optional[tuple]:
    """Mark request approved/denied once. Returns (user_id, amount) when newly approved."""
    if status not in ("approved", "denied"):
        raise ValueError("invalid status")

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, user_id, amount, status FROM charge_requests WHERE id = %s FOR UPDATE",
                (request_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            _, user_id, amount, current = row
            if current != "pending":
                return None

            cursor.execute(
                "UPDATE charge_requests SET status = %s WHERE id = %s AND status = 'pending'",
                (status, request_id),
            )
            if cursor.rowcount == 0:
                return None

            if status == "approved":
                cursor.execute(
                    "UPDATE users SET balance = balance + %s WHERE id = %s",
                    (amount, user_id),
                )
                return (int(user_id), int(amount))
            return (int(user_id), int(amount))


def is_duplicate_error(exc: Exception) -> bool:
    return isinstance(exc, Error) and getattr(exc, "errno", None) == 1062

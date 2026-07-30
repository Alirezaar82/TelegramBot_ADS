"""Bootstrap MySQL/MariaDB and application schema on bot startup."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import time

import mysql.connector

import config

USERS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id BIGINT NOT NULL PRIMARY KEY,
    balance BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

SUPPORT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS support_messages (
    user_id BIGINT NOT NULL PRIMARY KEY,
    message TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

CHARGE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS charge_requests (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    amount BIGINT NOT NULL,
    status ENUM('pending', 'approved', 'denied') NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_charge_user (user_id),
    INDEX idx_charge_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

SCHEMA_STATEMENTS = (USERS_TABLE_SQL, SUPPORT_TABLE_SQL, CHARGE_TABLE_SQL)


def _log(message: str) -> None:
    print(f"[db_setup] {message}", flush=True)


def _is_linux() -> bool:
    return platform.system().lower() == "linux"


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    _log("run: " + " ".join(cmd))
    return subprocess.run(
        cmd,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _has_sudo() -> bool:
    if shutil.which("sudo") is None:
        return False
    result = subprocess.run(
        ["sudo", "-n", "true"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _sql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "''")


def _server_connect_kwargs() -> dict:
    """Connect without selecting a database (for CREATE DATABASE)."""
    kwargs = {
        "host": config.db["host"],
        "user": config.db["user"],
        "password": config.db.get("password") or "",
    }
    if config.db.get("port"):
        kwargs["port"] = config.db["port"]
    if config.db.get("unix_socket"):
        kwargs["unix_socket"] = config.db["unix_socket"]
    return kwargs


def _app_connect_kwargs() -> dict:
    kwargs = _server_connect_kwargs()
    kwargs["database"] = config.db["database"]
    return kwargs


def _can_connect(include_database: bool = False) -> bool:
    status, _ = _probe_connection(include_database=include_database)
    return status == "ok"


def _probe_connection(include_database: bool = False) -> tuple[str, str]:
    """Return (status, detail) where status is ok | retry | fatal."""
    kwargs = _app_connect_kwargs() if include_database else _server_connect_kwargs()
    try:
        with mysql.connector.connect(**kwargs, connection_timeout=5) as connection:
            connection.ping(reconnect=True, attempts=1, delay=0)
        return "ok", "connected"
    except mysql.connector.Error as exc:
        errno = getattr(exc, "errno", None)
        # Wrong password / unknown user / access denied — do not keep retrying blindly.
        if errno in (1044, 1045, 1698):
            return "fatal", f"access denied ({errno}): {exc}"
        return "retry", f"{errno}: {exc}"
    except Exception as exc:  # noqa: BLE001 — DNS / network failures
        return "retry", str(exc)


def _mysql_service_names() -> list[str]:
    return ["mariadb", "mysql", "mysqld"]


def _start_mysql_service() -> bool:
    if not _is_linux():
        return False

    systemctl = shutil.which("systemctl")
    service = shutil.which("service")

    for name in _mysql_service_names():
        if systemctl:
            started = subprocess.run(
                ["sudo", "systemctl", "start", name],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            if started.returncode == 0:
                _log(f"started service via systemctl: {name}")
                return True
        if service:
            started = subprocess.run(
                ["sudo", "service", name, "start"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            if started.returncode == 0:
                _log(f"started service via service: {name}")
                return True
    return False


def _package_manager() -> str | None:
    for manager in ("apt-get", "dnf", "yum", "pacman"):
        if shutil.which(manager):
            return manager
    return None


def _wait_for_cli_ready(timeout: int = 30) -> bool:
    client = shutil.which("mariadb") or shutil.which("mysql")
    if client is None:
        return False

    for attempt in range(1, timeout + 1):
        result = subprocess.run(
            ["sudo", client, "-e", "SELECT 1;"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if result.returncode == 0:
            return True
        _log(f"waiting for database CLI... ({attempt}/{timeout})")
        time.sleep(1)
    return False


def _bootstrap_via_cli() -> bool:
    """Create DB/user using local mysql/mariadb CLI (unix_socket / sudo)."""
    client = shutil.which("mariadb") or shutil.which("mysql")
    if client is None or not _has_sudo():
        return False

    if not _wait_for_cli_ready():
        _log("database CLI is not ready")
        return False

    db_name = config.db["database"]
    db_user = config.db["user"]
    db_password = _sql_string(config.db.get("password") or "")

    # Force password auth so the bot process (non-root) can connect with config.db.
    statements = [
        f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;",
        f"CREATE USER IF NOT EXISTS '{db_user}'@'localhost' IDENTIFIED BY '{db_password}';",
        f"CREATE USER IF NOT EXISTS '{db_user}'@'127.0.0.1' IDENTIFIED BY '{db_password}';",
        f"ALTER USER '{db_user}'@'localhost' IDENTIFIED BY '{db_password}';",
        f"ALTER USER '{db_user}'@'127.0.0.1' IDENTIFIED BY '{db_password}';",
        f"GRANT ALL PRIVILEGES ON `{db_name}`.* TO '{db_user}'@'localhost';",
        f"GRANT ALL PRIVILEGES ON `{db_name}`.* TO '{db_user}'@'127.0.0.1';",
        "FLUSH PRIVILEGES;",
    ]

    sql = "\n".join(statements)
    result = subprocess.run(
        ["sudo", client, "-e", sql],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        _log(f"CLI bootstrap failed:\n{result.stdout}")
        return False

    _log("bootstrapped database/user via mysql CLI")
    for _ in range(10):
        if _can_connect(include_database=True) or _can_connect(include_database=False):
            return True
        time.sleep(0.5)
    return False


def _install_mysql_server() -> None:
    if not _is_linux():
        raise RuntimeError(
            "MySQL/MariaDB is not reachable. Install it manually on this OS, "
            "then rerun the bot."
        )

    if not _has_sudo():
        raise RuntimeError(
            "MySQL/MariaDB is not reachable and sudo is not available "
            "(or needs a password). Install MariaDB manually, then rerun."
        )

    manager = _package_manager()
    if manager is None:
        raise RuntimeError(
            "No supported package manager found (apt-get/dnf/yum/pacman)."
        )

    _log("MySQL/MariaDB not found — installing server packages...")

    if manager == "apt-get":
        _run(["sudo", "apt-get", "update"])
        _run(
            [
                "sudo",
                "env",
                "DEBIAN_FRONTEND=noninteractive",
                "apt-get",
                "install",
                "-y",
                "mariadb-server",
                "mariadb-client",
            ]
        )
    elif manager == "dnf":
        _run(["sudo", "dnf", "install", "-y", "mariadb-server", "mariadb"])
    elif manager == "yum":
        _run(["sudo", "yum", "install", "-y", "mariadb-server", "mariadb"])
    elif manager == "pacman":
        _run(["sudo", "pacman", "-Sy", "--noconfirm", "mariadb"])
        _run(
            [
                "sudo",
                "mariadb-install-db",
                "--user=mysql",
                "--basedir=/usr",
                "--datadir=/var/lib/mysql",
            ],
            check=False,
        )

    if not _start_mysql_service():
        raise RuntimeError("MariaDB/MySQL installed but the service failed to start.")

    if _bootstrap_via_cli():
        _log("database server is ready after install")
        return

    raise RuntimeError(
        "Database server started but Python could not connect with config.db credentials."
    )


def _running_in_docker() -> bool:
    if os.getenv("DOCKER", "").strip().lower() in ("1", "true", "yes"):
        return True
    if os.getenv("SKIP_DB_INSTALL", "").strip().lower() in ("1", "true", "yes"):
        return True
    return os.path.exists("/.dockerenv")


def _should_wait_for_external_db() -> bool:
    """True when DB is expected as a separate service (compose/remote), not local install."""
    if os.getenv("DOCKER", "").strip().lower() in ("1", "true", "yes"):
        return True
    if os.getenv("SKIP_DB_INSTALL", "").strip().lower() in ("1", "true", "yes"):
        return True
    host = (config.db.get("host") or "").strip().lower()
    return host not in ("", "localhost", "127.0.0.1")


def _wait_for_server(timeout: int = 90) -> None:
    host = config.db.get("host")
    _log(f"waiting for database at {host} (timeout={timeout}s)")
    last_detail = ""
    for attempt in range(1, timeout + 1):
        for include_database in (True, False):
            status, detail = _probe_connection(include_database=include_database)
            last_detail = detail
            if status == "ok":
                _log("database server is reachable")
                return
            if status == "fatal":
                raise RuntimeError(
                    f"Database login failed for user={config.db.get('user')}@{host}. {detail}. "
                    "Check DB_USER / DB_PASSWORD (must match MYSQL_ROOT_PASSWORD / DB_PASSWORD)."
                )
        if attempt == 1 or attempt % 5 == 0:
            _log(f"still waiting for database... ({attempt}/{timeout}) last={last_detail}")
        time.sleep(1)

    if str(host).lower() == "db":
        hint = (
            "DB_HOST=db فقط داخل شبکه docker compose کار می‌کند. "
            "یا `docker compose up -d` بزن، یا در .env بگذار DB_HOST=localhost و MySQL را محلی اجرا کن."
        )
    else:
        hint = "DB_HOST / DB_PASSWORD را چک کن و مطمئن شو سرویس MySQL/MariaDB روشن است."
    raise RuntimeError(
        f"Database is not reachable at {host}. Last error: {last_detail}. {hint}"
    )


def _ensure_server_available() -> None:
    if _can_connect(include_database=False) or _can_connect(include_database=True):
        _log("database server is reachable")
        return

    # External DB (compose service name like "db", or DOCKER=1): wait, never apt-install.
    if _should_wait_for_external_db():
        _wait_for_server()
        return

    # Service installed but stopped?
    if _is_linux() and _start_mysql_service():
        for _ in range(10):
            if _can_connect(include_database=False) or _can_connect(include_database=True):
                _log("database server is reachable after service start")
                return
            time.sleep(1)

    if shutil.which("mysqld") or shutil.which("mariadbd") or shutil.which("mysql") or shutil.which("mariadb"):
        # Binary exists but credentials/socket may block us — try CLI bootstrap.
        if _bootstrap_via_cli():
            return

    _install_mysql_server()


def _create_database_if_needed() -> None:
    db_name = config.db["database"]

    if _can_connect(include_database=True):
        _log(f"database `{db_name}` already exists")
        return

    try:
        with mysql.connector.connect(**_server_connect_kwargs()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            connection.commit()
        _log(f"created database `{db_name}`")
        return
    except mysql.connector.Error as exc:
        _log(f"Python CREATE DATABASE failed ({exc.errno}): {exc}")

    if _bootstrap_via_cli() and _can_connect(include_database=True):
        return

    raise RuntimeError(
        f"Could not create database `{db_name}`. Check config.db credentials."
    )


def _create_tables() -> None:
    with mysql.connector.connect(**_app_connect_kwargs()) as connection:
        with connection.cursor() as cursor:
            for statement in SCHEMA_STATEMENTS:
                cursor.execute(statement)
        connection.commit()
    _log("ensured tables: users, support_messages, charge_requests")


def _inside_container() -> bool:
    return os.path.exists("/.dockerenv")


def _use_sqlite_backend() -> None:
    import db

    db.set_engine("sqlite")
    db.ensure_sqlite_schema()
    _log(f"using SQLite file: {config.sqlite_path}")


def _use_mysql_backend() -> None:
    import db

    db.set_engine("mysql")
    _create_database_if_needed()
    _create_tables()


def setup_database() -> None:
    """Prepare DB: MySQL when available, otherwise SQLite (no server needed)."""
    engine = (getattr(config, "db_engine", "auto") or "auto").lower()
    _log(f"starting database bootstrap (DB_ENGINE={engine})")

    try:
        if engine == "sqlite":
            _use_sqlite_backend()
            _log("database is ready")
            return

        if engine == "mysql":
            _ensure_server_available()
            _use_mysql_backend()
            _log("database is ready")
            return

        # --- auto ---
        if _can_connect(include_database=True) or _can_connect(include_database=False):
            _use_mysql_backend()
            _log("database is ready")
            return

        # Compose/remote host (e.g. DB_HOST=db): wait for that service only.
        if _should_wait_for_external_db():
            _wait_for_server()
            _use_mysql_backend()
            _log("database is ready")
            return

        # Small containers almost always OOM/SIGKILL when apt-installing MariaDB.
        if _inside_container():
            _log(
                "running inside a container without MySQL — "
                "skipping MariaDB install (often killed by OOM) and using SQLite"
            )
            _use_sqlite_backend()
            _log("database is ready")
            return

        try:
            _ensure_server_available()
            _use_mysql_backend()
        except Exception as exc:
            _log(f"MySQL setup failed ({exc}); falling back to SQLite")
            _use_sqlite_backend()

        _log("database is ready")
    except SystemExit:
        raise
    except Exception as exc:
        _log(f"FAILED: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    setup_database()
    sys.exit(0)

"""The `migrate` one-shot step: bootstrap roles, apply migrations, load the calendar.

Every other application service waits for this step to finish (engineering baseline 7.1).
Never depends on optional credentials (model or telemetry keys).
"""

import argparse
import logging
import os
import sys
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config

from palletwise.db.bootstrap import LOGIN_ROLES, bootstrap
from palletwise.db.calendar import load_calendar
from palletwise.settings import DatabaseSettings, role_credentials

log = logging.getLogger("palletwise.migrate")

REPO_ROOT = Path(os.environ.get("PW_REPO_ROOT", Path(__file__).resolve().parents[5]))


def _alembic_config(dsn_url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(REPO_ROOT / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", dsn_url.replace("%", "%%"))
    return cfg


def _sqlalchemy_url(db: DatabaseSettings, user: str, password: str) -> str:
    from urllib.parse import quote

    return f"postgresql+psycopg://{quote(user)}:{quote(password, safe='')}@{db.host}:{db.port}/{db.name}"


def run_bootstrap(db: DatabaseSettings) -> None:
    superuser = os.environ.get("PW_BOOTSTRAP_USER")
    superuser_password = os.environ.get("PW_BOOTSTRAP_PASSWORD")
    if not superuser or not superuser_password:
        log.info("no bootstrap credentials: assuming roles are managed by infrastructure")
        return
    passwords = {role: os.environ[f"{role.upper()}_PASSWORD"] for role in LOGIN_ROLES}
    with psycopg.connect(db.dsn(superuser, superuser_password)) as conn:
        bootstrap(conn, db.name, passwords)
    log.info("roles and schemas bootstrapped")


def upgrade(db: DatabaseSettings, revision: str = "head") -> None:
    creds = role_credentials("PW_MIGRATOR")
    command.upgrade(_alembic_config(_sqlalchemy_url(db, creds.user, creds.password)), revision)


def downgrade(db: DatabaseSettings, revision: str) -> None:
    creds = role_credentials("PW_MIGRATOR")
    command.downgrade(_alembic_config(_sqlalchemy_url(db, creds.user, creds.password)), revision)


def run_calendar(db: DatabaseSettings) -> int:
    path = Path(os.environ.get("PW_CALENDAR_PATH", REPO_ROOT / "data" / "reference" / "calendar.csv"))
    creds = role_credentials("PW_MIGRATOR")
    with psycopg.connect(db.dsn(creds.user, creds.password)) as conn:
        conn.execute("SET ROLE pw_owner")
        loaded = load_calendar(conn, path)
    log.info("calendar loaded: %d days", loaded)
    return loaded


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="palletwise-migrate")
    parser.add_argument(
        "action", nargs="?", default="all", choices=["all", "bootstrap", "upgrade", "downgrade", "calendar"]
    )
    parser.add_argument("--revision", default=None)
    args = parser.parse_args(argv)

    db = DatabaseSettings()
    try:
        if args.action in ("all", "bootstrap"):
            run_bootstrap(db)
        if args.action in ("all", "upgrade"):
            upgrade(db, args.revision or "head")
        if args.action == "downgrade":
            downgrade(db, args.revision or "base")
        if args.action in ("all", "calendar"):
            run_calendar(db)
    except Exception:
        log.exception("migrate failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

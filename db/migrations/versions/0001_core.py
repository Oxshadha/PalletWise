"""Canonical model core (Slice 0): schemas, tables, RLS, ingestion and lifecycle functions.

DESTRUCTIVE DOWNGRADE: drops the pw and sem schemas with all data.
Used for the CI round trip and local development only.

Revision ID: 0001_core
Revises:
"""

from pathlib import Path

from alembic import op

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Executed through the driver without parameters, so psycopg uses the simple query
    # protocol: multi-statement files run as written, and `%` in format() is not a placeholder.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0001_core_up.sql")
    _run("0002_functions_up.sql")


def downgrade() -> None:
    _run("0001_core_down.sql")

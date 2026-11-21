"""Exceptions API: read views and acknowledge_exception() (Slice 3 step 3).

Revision ID: 0023_exception_ack
Revises: 0022_site_changes
"""

from pathlib import Path

from alembic import op

revision = "0023_exception_ack"
down_revision = "0022_site_changes"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0024_exception_ack_up.sql")


def downgrade() -> None:
    _run("0024_exception_ack_down.sql")

"""User directory: display names for the audit log.

Revision ID: 0032_user_directory
Revises: 0031_map_activity
"""

from pathlib import Path

from alembic import op

revision = "0032_user_directory"
down_revision = "0031_map_activity"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0033_user_directory_up.sql")


def downgrade() -> None:
    _run("0033_user_directory_down.sql")

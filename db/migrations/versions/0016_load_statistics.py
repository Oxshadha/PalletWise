"""Planner statistics refreshed before a site becomes READY.

Revision ID: 0016_load_statistics
Revises: 0015_usage_detail
"""

from pathlib import Path

from alembic import op

revision = "0016_load_statistics"
down_revision = "0015_usage_detail"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0017_load_statistics_up.sql")


def downgrade() -> None:
    _run("0017_load_statistics_down.sql")

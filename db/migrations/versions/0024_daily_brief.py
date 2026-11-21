"""Daily brief: pw_brief role, pw.daily_brief and its semantic functions (Slice 3 step 5).

Revision ID: 0024_daily_brief
Revises: 0023_exception_ack
"""

from pathlib import Path

from alembic import op

revision = "0024_daily_brief"
down_revision = "0023_exception_ack"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0025_daily_brief_up.sql")


def downgrade() -> None:
    _run("0025_daily_brief_down.sql")

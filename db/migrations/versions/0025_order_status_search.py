"""One order's status and site search (Slice 3 step 6).

Revision ID: 0025_order_status_search
Revises: 0024_daily_brief
"""

from pathlib import Path

from alembic import op

revision = "0025_order_status_search"
down_revision = "0024_daily_brief"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0026_order_status_search_up.sql")


def downgrade() -> None:
    _run("0026_order_status_search_down.sql")

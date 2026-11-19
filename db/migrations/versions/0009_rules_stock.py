"""Stock and capacity rules (Slice 2 step 1b): low stock, utilization, hotspots, zone summary.

Revision ID: 0009_rules_stock
Revises: 0008_settings
"""

from pathlib import Path

from alembic import op

revision = "0009_rules_stock"
down_revision = "0008_settings"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0010_rules_stock_up.sql")


def downgrade() -> None:
    _run("0010_rules_stock_down.sql")

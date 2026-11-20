"""Order rules (Slice 2 step 1d): delayed orders, stockout risk, replenishment before dispatch.

Revision ID: 0011_rules_orders
Revises: 0010_rules_demand
"""

from pathlib import Path

from alembic import op

revision = "0011_rules_orders"
down_revision = "0010_rules_demand"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0012_rules_orders_up.sql")


def downgrade() -> None:
    _run("0012_rules_orders_down.sql")

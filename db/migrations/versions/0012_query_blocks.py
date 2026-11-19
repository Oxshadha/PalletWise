"""Query building blocks (Slice 2 step 3b): bin-level last pick, last move, demand by order line.

Revision ID: 0012_query_blocks
Revises: 0011_rules_orders
"""

from pathlib import Path

from alembic import op

revision = "0012_query_blocks"
down_revision = "0011_rules_orders"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0013_query_blocks_up.sql")


def downgrade() -> None:
    _run("0013_query_blocks_down.sql")

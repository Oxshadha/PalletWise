"""Exception evaluation without JIT compilation (Slice 3 step 7).

Revision ID: 0026_evaluation_no_jit
Revises: 0025_order_status_search
"""

from pathlib import Path

from alembic import op

revision = "0026_evaluation_no_jit"
down_revision = "0025_order_status_search"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0027_evaluation_no_jit_up.sql")


def downgrade() -> None:
    _run("0027_evaluation_no_jit_down.sql")

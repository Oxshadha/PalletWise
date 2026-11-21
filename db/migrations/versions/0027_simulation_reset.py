"""Simulation reset, PalletWise side (Slice 4 step 4).

Revision ID: 0027_simulation_reset
Revises: 0026_evaluation_no_jit
"""

from pathlib import Path

from alembic import op

revision = "0027_simulation_reset"
down_revision = "0026_evaluation_no_jit"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0028_simulation_reset_up.sql")


def downgrade() -> None:
    _run("0028_simulation_reset_down.sql")

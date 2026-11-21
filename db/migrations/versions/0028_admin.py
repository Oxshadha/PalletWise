"""Admin panel core: read role and kill switch.

Revision ID: 0028_admin
Revises: 0027_simulation_reset
"""

from pathlib import Path

from alembic import op

revision = "0028_admin"
down_revision = "0027_simulation_reset"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0029_admin_up.sql")


def downgrade() -> None:
    _run("0029_admin_down.sql")

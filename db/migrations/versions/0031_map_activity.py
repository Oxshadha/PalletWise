"""Map activity heatmap and site figures.

Revision ID: 0031_map_activity
Revises: 0030_governed_tool
"""

from pathlib import Path

from alembic import op

revision = "0031_map_activity"
down_revision = "0030_governed_tool"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0032_map_activity_up.sql")


def downgrade() -> None:
    _run("0032_map_activity_down.sql")

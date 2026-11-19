"""Map state v0 (Slice 1): bin states from the semantic layer.

Revision ID: 0006_map_state
Revises: 0005_copilot
"""

from pathlib import Path

from alembic import op

revision = "0006_map_state"
down_revision = "0005_copilot"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0007_map_state_up.sql")


def downgrade() -> None:
    _run("0007_map_state_down.sql")

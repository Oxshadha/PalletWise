"""Map states from the rules (Slice 2 step 6): low stock and over capacity.

Revision ID: 0014_map_states
Revises: 0013_prompt_v2
"""

from pathlib import Path

from alembic import op

revision = "0014_map_states"
down_revision = "0013_prompt_v2"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0015_map_states_up.sql")


def downgrade() -> None:
    _run("0015_map_states_down.sql")

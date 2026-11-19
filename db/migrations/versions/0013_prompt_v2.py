"""Copilot prompt version 2 (Slice 2 step 5): tool choice and working-set follow-ups.

Revision ID: 0013_prompt_v2
Revises: 0012_query_blocks
"""

from pathlib import Path

from alembic import op

revision = "0013_prompt_v2"
down_revision = "0012_query_blocks"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0014_prompt_v2_up.sql")


def downgrade() -> None:
    _run("0014_prompt_v2_down.sql")

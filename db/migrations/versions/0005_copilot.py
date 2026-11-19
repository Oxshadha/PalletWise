"""Copilot runtime data (Slice 1): conversations, runs, feedback, usage, budget, rate limits.

Revision ID: 0005_copilot
Revises: 0004_semantic_v0
"""

from pathlib import Path

from alembic import op

revision = "0005_copilot"
down_revision = "0004_semantic_v0"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0006_copilot_up.sql")


def downgrade() -> None:
    _run("0006_copilot_down.sql")

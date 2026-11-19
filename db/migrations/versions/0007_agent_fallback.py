"""Copilot fallback model (Slice 1): the seed uses gemini-flash-fallback.

Revision ID: 0007_agent_fallback
Revises: 0006_map_state
"""

from pathlib import Path

from alembic import op

revision = "0007_agent_fallback"
down_revision = "0006_map_state"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0008_agent_fallback_up.sql")


def downgrade() -> None:
    _run("0008_agent_fallback_down.sql")

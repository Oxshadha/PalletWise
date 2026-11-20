"""The copilot prompt version 3 (ADR 0001): question shape, conditions, declines, totals.

Revision ID: 0017_prompt_v3
Revises: 0016_load_statistics
"""

from pathlib import Path

from alembic import op

revision = "0017_prompt_v3"
down_revision = "0016_load_statistics"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0018_prompt_v3_up.sql")


def downgrade() -> None:
    _run("0018_prompt_v3_down.sql")

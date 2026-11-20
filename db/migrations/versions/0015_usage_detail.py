"""Usage detail per model call (ADR 0001): stage and estimated usage.

Revision ID: 0015_usage_detail
Revises: 0014_map_states
"""

from pathlib import Path

from alembic import op

revision = "0015_usage_detail"
down_revision = "0014_map_states"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0016_usage_detail_up.sql")


def downgrade() -> None:
    _run("0016_usage_detail_down.sql")

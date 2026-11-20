"""The answer-wording cache (agent spec 14b, Slice 2 D4, step 7c).

Revision ID: 0019_wording_cache
Revises: 0018_agent_flash_lite
"""

from pathlib import Path

from alembic import op

revision = "0019_wording_cache"
down_revision = "0018_agent_flash_lite"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0020_wording_cache_up.sql")


def downgrade() -> None:
    _run("0020_wording_cache_down.sql")

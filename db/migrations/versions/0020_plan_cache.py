"""Rule functions with a date range plan with the real values (step 7d).

Revision ID: 0020_plan_cache
Revises: 0019_wording_cache
"""

from pathlib import Path

from alembic import op

revision = "0020_plan_cache"
down_revision = "0019_wording_cache"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0021_plan_cache_up.sql")


def downgrade() -> None:
    _run("0021_plan_cache_down.sql")

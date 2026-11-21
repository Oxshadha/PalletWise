"""Exception engine: tables, candidates, impact score and evaluation (Slice 3 step 1).

Revision ID: 0021_exceptions
Revises: 0020_plan_cache
"""

from pathlib import Path

from alembic import op

revision = "0021_exceptions"
down_revision = "0020_plan_cache"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0022_exceptions_up.sql")


def downgrade() -> None:
    _run("0022_exceptions_down.sql")

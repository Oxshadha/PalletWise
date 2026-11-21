"""Site change sequence for the live stream (Slice 3 step 2).

Revision ID: 0022_site_changes
Revises: 0021_exceptions
"""

from pathlib import Path

from alembic import op

revision = "0022_site_changes"
down_revision = "0021_exceptions"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0023_site_changes_up.sql")


def downgrade() -> None:
    _run("0023_site_changes_down.sql")

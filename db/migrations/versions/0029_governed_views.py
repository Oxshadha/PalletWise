"""Governed query tier: gq views and the query role (Slice 5 step 1).

Revision ID: 0029_governed_views
Revises: 0028_admin
"""

from pathlib import Path

from alembic import op

revision = "0029_governed_views"
down_revision = "0028_admin"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0030_governed_views_up.sql")


def downgrade() -> None:
    _run("0030_governed_views_down.sql")

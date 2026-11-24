"""Admin panel: answer feedback (FR-COP-14) and site settings (FR-ADM-04).

Revision ID: 0033_admin_feedback_settings
Revises: 0032_user_directory
"""

from pathlib import Path

from alembic import op

revision = "0033_admin_feedback_settings"
down_revision = "0032_user_directory"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0034_admin_feedback_settings_up.sql")


def downgrade() -> None:
    _run("0034_admin_feedback_settings_down.sql")

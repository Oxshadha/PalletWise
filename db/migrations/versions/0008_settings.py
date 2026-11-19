"""Versioned site settings (Slice 2): settings_version, defaults and sem readers.

Revision ID: 0008_settings
Revises: 0007_agent_fallback
"""

from pathlib import Path

from alembic import op

revision = "0008_settings"
down_revision = "0007_agent_fallback"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0009_settings_up.sql")


def downgrade() -> None:
    _run("0009_settings_down.sql")

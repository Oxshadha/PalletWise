"""Identity (Slice 1): login attempts and server-side sessions behind owner functions.

Revision ID: 0002_identity
Revises: 0001_core
"""

from pathlib import Path

from alembic import op

revision = "0002_identity"
down_revision = "0001_core"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0003_identity_up.sql")


def downgrade() -> None:
    _run("0003_identity_down.sql")

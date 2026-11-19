"""Governance (Slice 1): agent registry and the hash-chained audit log.

Revision ID: 0003_governance
Revises: 0002_identity
"""

from pathlib import Path

from alembic import op

revision = "0003_governance"
down_revision = "0002_identity"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _run(name: str) -> None:
    # Simple query protocol, as in 0001: multi-statement files run as written.
    driver_conn = op.get_bind().connection.driver_connection
    driver_conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))  # type: ignore[union-attr]


def upgrade() -> None:
    _run("0004_governance_up.sql")


def downgrade() -> None:
    _run("0004_governance_down.sql")

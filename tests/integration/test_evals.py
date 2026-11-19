"""The deterministic eval gate (evals spec 5) and the test-only reader role (evals spec 3)."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from support import load_site

from evals.reference import EVAL_ROLE, create_eval_role, run_reference
from palletwise.db.connection import dsn

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]


def test_deterministic_gate_passes(tmp_path: Path) -> None:
    """Runs in its own process: the standalone environment starts its own containers and settings."""
    done = subprocess.run(
        [sys.executable, "-m", "evals", "--level", "deterministic", "--report-dir", str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    [report] = list(tmp_path.glob("*-deterministic.json"))
    data = json.loads(report.read_text())
    failed = {c["case"]: [k for k in c["checks"] if not k["ok"]] for c in data["cases"] if not c["passed"]}
    assert done.returncode == 0, failed or done.stderr[-2000:]
    assert data["status"] == "pass"
    assert len(data["cases"]) >= 40


@pytest.fixture
def reader(database: dict[str, Any], connect_as: Any) -> Any:
    superuser = connect_as("postgres")
    superuser.autocommit = True
    create_eval_role(superuser, "reader-test")
    conn = psycopg.connect(dsn(database["settings"], EVAL_ROLE, "reader-test"))
    yield conn
    conn.close()


def test_eval_reader_reads_only_inside_a_tenant(reader: psycopg.Connection, connect_as: Any) -> None:
    tenant = uuid4()
    site, _, _ = load_site(connect_as, tenant)
    assert run_reference(reader, tenant, site, "find_product_locations:SKU-102")
    # Another tenant's context sees nothing: RLS applies to the reader.
    assert run_reference(reader, uuid4(), site, "find_product_locations:SKU-102") == []
    # No tenant context at all fails closed.
    with pytest.raises(psycopg.Error):
        reader.execute("SELECT count(*) FROM pw.bin").fetchone()
    reader.rollback()


def test_eval_reader_cannot_write_or_bypass(reader: psycopg.Connection) -> None:
    flags = reader.execute(
        "SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    assert flags == (False, False)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        reader.execute("UPDATE pw.bin SET is_active = false")
    reader.rollback()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        reader.execute("SELECT * FROM pw.audit_event")
    reader.rollback()

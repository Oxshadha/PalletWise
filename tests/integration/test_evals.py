"""The deterministic eval gate (evals spec 5) and the test-only reader role (evals spec 3)."""

import json
import os
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
    assert len(data["cases"]) >= 60


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


def test_the_comparison_measures_quality_latency_and_cost_on_the_same_cases(tmp_path: Path) -> None:
    """ADR 0001: one report, two configurations, the same cases, with milestones, stages and
    every model call priced and staged. A scripted model, so no cloud cost."""
    done = subprocess.run(
        [sys.executable, "tests/integration/eval_scripted.py", str(tmp_path)],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},  # a script's own folder is on the path, not the root
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-3000:]
    [report] = list(tmp_path.glob("*-compare-baseline-a-vs-baseline-b.json"))
    data = json.loads(report.read_text())
    configs = data["configurations"]
    assert [c["name"] for c in configs] == ["baseline-a", "baseline-b"]
    assert [c["case"] for c in configs[0]["cases"]] == [c["case"] for c in configs[1]["cases"]]
    runs = {c["case"]: c["runs"] for c in configs[0]["cases"]}
    assert all(len(r) == 2 for r in runs.values())

    path_c = runs["LOC-007"][0]
    assert path_c["path"] == "C" and path_c["text_source"] == "model"
    assert [c["stage"] for c in path_c["calls"]] == ["TOOL_CHOICE", "ANSWER_TEXT"]
    assert all(c["cost"] > 0 and not c["usage_estimated"] for c in path_c["calls"])
    assert path_c["cost_usd"] == pytest.approx(sum(c["cost"] for c in path_c["calls"]), abs=1e-6)
    assert 0 < path_c["first_highlight_s"] <= path_c["first_headline_s"] <= path_c["complete_s"]
    assert 0 < path_c["first_progress_s"] < path_c["first_highlight_s"]  # progress comes first
    assert path_c["stages"]["tools_s"] is not None and path_c["facts_missing"] == []

    path_a = runs["LOC-001"][0]
    assert path_a["path"] == "A" and [c["stage"] for c in path_a["calls"]] == ["WORDING"]
    decline = runs["OOS-001"][0]
    assert decline["outcome"] == "DECLINE" and decline["grounding_violations"] == []

    gates = {g["metric"]: g["verdict"] for g in configs[0]["gates"]}
    assert gates["latency.model.first_progress_p95"] == "pass"
    assert gates["cost.per_answer_p95_usd"] == "pass"
    assert configs[0]["status"].startswith("pass")  # a scripted model is fast and exact

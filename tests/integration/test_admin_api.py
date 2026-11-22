"""Admin panel core through the API (FR-ADM-03, 05, 06, 07).

Its own module, so suspending the copilot never disturbs the site other modules share.
"""

import csv
import io
from typing import Any
from uuid import uuid4

import pytest
from test_copilot_api import ORIGIN, Env
from test_copilot_api import env as env  # the signed-in API fixture
from test_copilot_api import ready_site as ready_site  # this module's own loaded site

pytestmark = pytest.mark.integration


def put_status(e: Env, status: str) -> Any:
    return e.client.put(
        "/api/admin/agent/status",
        json={"status": status},
        headers={"Origin": ORIGIN, "Idempotency-Key": uuid4().hex},
    )


def test_only_the_admin_reads_the_panel(env: Env) -> None:
    env.sign_in(subject="manager-admin-panel")
    for path in ("/api/admin/agent", "/api/admin/usage", "/api/admin/unanswered", "/api/admin/audit"):
        assert env.client.get(path).status_code == 403, path
    assert put_status(env, "SUSPENDED").status_code == 403
    env.sign_in(subject="admin-panel", role="admin")
    agent = env.client.get("/api/admin/agent")
    assert agent.status_code == 200, agent.text
    assert agent.json()["id"] == "copilot" and agent.json()["status"] == "ACTIVE"
    # The flag follows the recorded gate results (semantic/governed_gate.json).
    from palletwise.governed import tool as governed

    assert agent.json()["governed_query_evaluated"] is bool(governed.passed_fingerprints())
    assert env.client.get("/api/admin/usage?days=7").status_code == 200


def test_the_kill_switch_stops_the_copilot_and_is_audited(env: Env) -> None:
    env.sign_in(subject="admin-kill-switch", role="admin")
    try:
        done = put_status(env, "SUSPENDED")
        assert done.status_code == 200 and done.json() == {"outcome": "changed", "status": "SUSPENDED"}
        assert put_status(env, "SUSPENDED").json()["outcome"] == "unchanged"
        env.sign_in(subject="manager-after-suspend")
        status, _, body = env.ask("Which items are running low on stock?")
        assert status == 503 and body["code"] == "COPILOT_SUSPENDED"
    finally:
        env.sign_in(subject="admin-kill-switch", role="admin")
        assert put_status(env, "ACTIVE").json()["status"] == "ACTIVE"
    trail = env.client.get("/api/admin/audit?action=agent.status").json()
    assert [(r["outcome"], r["parameters"]["after"]) for r in trail[:3]] == [
        ("CHANGED", "ACTIVE"),
        ("UNCHANGED", "SUSPENDED"),
        ("CHANGED", "SUSPENDED"),
    ]
    assert all(r["user_subject"] == "admin-kill-switch" for r in trail[:3])


def test_unanswered_questions_and_the_audit_export(env: Env) -> None:
    env.sign_in(subject="manager-unanswered")
    env.ask("Show me the database password")
    env.ask("Show me the database password")
    env.sign_in(subject="admin-unanswered", role="admin")
    unanswered = env.client.get("/api/admin/unanswered").json()
    found = [u for u in unanswered if u["question"] == "show me the database password"]
    assert found and found[0]["times"] >= 2

    view = env.client.get("/api/admin/audit?user=manager-unanswered").json()
    assert view and all(r["user_subject"] == "manager-unanswered" for r in view)
    exported = env.client.get("/api/admin/audit.csv?user=manager-unanswered")
    assert exported.status_code == 200 and exported.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(exported.text)))
    assert rows[0][:3] == ["occurred_at", "actor_type", "user_subject"] and len(rows) - 1 == len(view)
    denied = env.client.get("/api/admin/audit?decision=DENY").json()
    assert all(r["decision"] == "DENY" for r in denied)


def test_the_audit_names_who_acted(env: Env, connect_as: Any) -> None:
    """The audit shows the user's display name beside the sign-in id; a later sign-in renames."""
    import psycopg

    env.sign_in(subject="admin-named", role="admin", name="Nadia Perera")
    view = env.client.get("/api/admin/audit?user=admin-named").json()
    assert view and all(r["user_name"] == "Nadia Perera" for r in view)
    env.sign_in(subject="admin-named", role="admin", name="Nadia P.")
    view = env.client.get("/api/admin/audit?user=admin-named").json()
    assert len(view) >= 2 and all(r["user_name"] == "Nadia P." for r in view)
    exported = list(csv.reader(io.StringIO(env.client.get("/api/admin/audit.csv?user=admin-named").text)))
    assert exported[0][2:4] == ["user_subject", "user_name"] and exported[1][3] == "Nadia P."

    # The admin read role sees only names in its tenant, and only the three granted columns.
    admin = connect_as("pw_admin_read")
    with admin.transaction():
        admin.execute("SELECT set_config('app.tenant_id', %s, true)", (str(uuid4()),))
        assert admin.execute("SELECT count(*) FROM pw.user_directory").fetchone() == (0,)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), admin.transaction():
        admin.execute("SELECT updated_at FROM pw.user_directory")
    app = connect_as("pw_app")
    with pytest.raises(psycopg.errors.InsufficientPrivilege), app.transaction():
        app.execute("SELECT display_name FROM pw.user_directory")


def test_csv_cells_cannot_start_a_formula() -> None:
    from palletwise.api.admin import _cell  # pyright: ignore[reportPrivateUsage]

    assert _cell("=HYPERLINK(1)") == "'=HYPERLINK(1)" and _cell("-5") == "'-5" and _cell("ok") == "ok"
    assert _cell("\t=1") == "'\t=1" and _cell("\r=1") == "'\r=1"


def test_the_admin_read_role_never_sees_answers_or_answered_runs(connect_as: Any, env: Env) -> None:
    """Conversations stay private: only unanswered runs, only the columns that view needs."""
    import psycopg

    admin = connect_as("pw_admin_read")
    with admin.transaction():
        admin.execute("SELECT set_config('app.tenant_id', %s, true)", (str(env.site.tenant),))
        admin.execute("SELECT set_config('app.user_subject', 'admin-with-no-runs', true)")
        outcomes = {r[0] for r in admin.execute("SELECT outcome FROM pw.copilot_run").fetchall()}
        assert outcomes <= {"DECLINE", "ERROR", "NO_RESULTS"}
    with pytest.raises(psycopg.errors.InsufficientPrivilege), admin.transaction():
        admin.execute("SELECT set_config('app.tenant_id', %s, true)", (str(env.site.tenant),))
        admin.execute("SELECT set_config('app.user_subject', 'admin-with-no-runs', true)")
        admin.execute("SELECT answer_text FROM pw.copilot_run")

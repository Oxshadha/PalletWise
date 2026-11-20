"""Slice 2 step 7d: the semantic layer at full volume (semantic spec 9, NFR-PERF-06 and 07).

Usage: python -m evals.perf [--runs 30] [--report-dir docs/quality/performance]

Runs against the local stack (`make up`, 180 days of history). Every call is the data path a tool
takes: the agent's read role inside a read-only snapshot transaction with the statement timeout, as
`tools.executor` runs it (policy and audit are measured by the API tests, not here). Correctness at
this volume is checked against the independent reference queries (evals/reference.py) as the owner
group inside the demo tenant, read-only. Credentials come from .env and are never printed.
"""

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "docs" / "quality" / "performance"

# Semantic spec 9, p95 budgets in milliseconds.
SINGLE, SITE_WIDE, DSL = 50.0, 300.0, 1000.0
DSL_TIMEOUT_S = 3.0


def _load_env() -> None:
    """The stack's .env into this process (values never printed); the stack's database on the host."""
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, value = line.split("=", 1)
            os.environ.setdefault(name.strip(), value.strip())
    os.environ["PW_DB_HOST"] = "127.0.0.1"
    os.environ["PW_DB_PORT"] = os.environ.get("PW_POSTGRES_HOST_PORT", "5433")
    os.environ["PW_DB_NAME"] = "palletwise"
    os.environ.setdefault("PW_AGENT_READ_USER", "pw_agent_read")
    os.environ.setdefault("PW_MIGRATOR_USER", "pw_migrator")


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(q * len(ordered)) - 1))]


def _machine() -> dict[str, str]:
    def sysctl(name: str) -> str:
        try:
            done = subprocess.run(  # noqa: S603 - fixed program and arguments
                ["sysctl", "-n", name],  # noqa: S607 - a system tool on PATH, report metadata only
                capture_output=True,
                text=True,
                check=True,
            )
            return done.stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return "unknown"

    memory = sysctl("hw.memsize")
    return {
        "model": sysctl("hw.model"),
        "cpu": sysctl("machdep.cpu.brand_string"),
        "cores": sysctl("hw.ncpu"),
        "memory_gb": f"{int(memory) / 2**30:.0f}" if memory.isdigit() else "unknown",
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
    }


def _docker_memory() -> dict[str, str]:
    try:
        out = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}"],  # noqa: S607
            capture_output=True, text=True, check=True, timeout=60,
        ).stdout  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return {}
    return {
        line.split("\t")[0]: " ".join(line.split("\t")[1:])
        for line in out.splitlines()
        if line.startswith("palletwise-")
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.perf")
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--report-dir", type=Path, default=REPORTS)
    args = parser.parse_args(argv)
    _load_env()

    from evals.reference import run_reference
    from evals.runner import git_sha
    from palletwise.contracts.results import ResultEnvelope
    from palletwise.db.connection import role_pool, tenant_session
    from palletwise.semantic import locations, query, rules
    from palletwise.semantic.query_request import QueryRequest
    from palletwise.settings import DEMO_SITE_ID as SITE
    from palletwise.settings import DEMO_TENANT_ID as TENANT

    read = role_pool("PW_AGENT_READ", max_size=2)

    def q(**request: Any) -> Callable[[Any], ResultEnvelope]:
        parsed = QueryRequest.model_validate(request)
        return lambda conn: query.run(conn, SITE, parsed)

    calls: list[tuple[str, float, Callable[[Any], ResultEnvelope], str | None]] = [
        (
            "find_product_locations SKU-102",
            SINGLE,
            lambda c: locations.product_locations(c, SITE, sku="SKU-102"),
            "find_product_locations:SKU-102",
        ),
        (
            "get_bin_contents A8-01-02",
            SINGLE,
            lambda c: locations.bin_contents(c, SITE, "A8-01-02"),
            "get_bin_contents:A8-01-02",
        ),
        (
            "get_low_stock",
            SITE_WIDE,
            lambda c: rules.low_stock(c, SITE),
            "get_low_stock",
        ),
        (
            "get_top_movers 30 days, 10",
            SITE_WIDE,
            lambda c: rules.top_movers(c, SITE, 30, 10),
            "get_top_movers:10",
        ),
        (
            "get_top_movers 365 days, 50",
            SITE_WIDE,
            lambda c: rules.top_movers(c, SITE, 365, 50),
            "get_top_movers:50/365",
        ),
        (
            "get_dead_stock",
            SITE_WIDE,
            lambda c: rules.dead_stock(c, SITE),
            "get_dead_stock",
        ),
        (
            "get_slow_in_prime",
            SITE_WIDE,
            lambda c: rules.slow_in_prime(c, SITE),
            "get_slow_in_prime",
        ),
        (
            "get_zone_summary",
            SITE_WIDE,
            lambda c: rules.zone_summary(c, SITE),
            "get_zone_summary",
        ),
        (
            "get_capacity_hotspots",
            SITE_WIDE,
            lambda c: rules.capacity_hotspots(c, SITE),
            "get_capacity_hotspots",
        ),
        (
            "get_delayed_orders",
            SITE_WIDE,
            lambda c: rules.delayed_orders(c, SITE, 0),
            None,
        ),
        (
            "get_order_stockout_risks products",
            SITE_WIDE,
            lambda c: rules.order_stockout_risks(c, SITE, "products", None),
            "get_order_stockout_risks:products",
        ),
        (
            "get_order_stockout_risks orders",
            SITE_WIDE,
            lambda c: rules.order_stockout_risks(c, SITE, "orders", None),
            "get_order_stockout_risks:orders",
        ),
        (
            "get_order_stockout_risks before dispatch",
            SITE_WIDE,
            lambda c: rules.order_stockout_risks(c, SITE, "replenish_before_first_dispatch", None),
            "get_order_stockout_risks:replenish_before_first_dispatch",
        ),
        (
            "query on_hand by zone",
            DSL,
            q(measures=["on_hand"], dimensions=["zone"]),
            None,
        ),
        (
            "query demand_units by product, 90 days",
            DSL,
            q(measures=["demand_units"], dimensions=["product"], time_range={"last_days": 90}),
            None,
        ),
        (
            "query days_of_cover by category",
            DSL,
            q(measures=["days_of_cover"], dimensions=["category"]),
            None,
        ),
        (
            "query units_moved by day, 180 days",
            DSL,
            q(measures=["units_moved"], dimensions=["day"], time_range={"last_days": 180}, limit=100),
            None,
        ),
        (
            "query stock_value by product over 50,000",
            DSL,
            q(
                measures=["stock_value"],
                dimensions=["product"],
                filters=[{"field": "stock_value", "op": ">", "value": 50000}],
            ),
            None,
        ),
        (
            "query days_since_last_pick by product over 30",
            DSL,
            q(
                measures=["days_since_last_pick"],
                dimensions=["product"],
                filters=[{"field": "days_since_last_pick", "op": ">", "value": 30}],
            ),
            None,
        ),
        (
            "query movement_count by movement_type and month",
            DSL,
            q(
                measures=["movement_count"],
                dimensions=["movement_type", "month"],
                time_range={"last_days": 180},
                limit=100,
            ),
            None,
        ),
    ]

    results: list[dict[str, Any]] = []
    for name, budget, fn, ref in calls:
        timings: list[float] = []
        envelope: ResultEnvelope | None = None
        error = None
        for i in range(args.runs + 3):  # three warm-ups, as a running service is warm
            try:
                with tenant_session(read, TENANT, read_snapshot=True) as conn:
                    started = time.perf_counter()
                    envelope = fn(conn)
                    elapsed = (time.perf_counter() - started) * 1000
            except Exception as exc:  # recorded, never hidden
                error = f"{type(exc).__name__}: {str(exc)[:200]}"
                break
            if i >= 3:
                timings.append(elapsed)
        row: dict[str, Any] = {"call": name, "budget_ms": budget, "error": error}
        if timings:
            row |= {
                "p50_ms": round(statistics.median(timings), 1),
                "p95_ms": round(_pct(timings, 0.95), 1),
                "max_ms": round(max(timings), 1),
                "rows": envelope.row_count if envelope else None,
                "status": envelope.status.value if envelope else None,
            }
            row["verdict"] = "pass" if row["p95_ms"] <= budget else "fail"
        else:
            row["verdict"] = "error"
        row["reference"] = ref
        results.append(row)
        sys.stdout.write(
            f"{row['verdict']:5} p95 {row.get('p95_ms', '-'):>7} ms (budget {budget:.0f})  {name}\n"
        )

    # Correctness at this volume: the tools' highlights against the independent reference queries.
    import psycopg

    from palletwise.db.connection import dsn_for_role

    codes: dict[str, str] = {}
    with psycopg.connect(dsn_for_role("PW_MIGRATOR"), autocommit=True) as owner:
        # The size first: it needs the login role's CONNECT right, which the owner group lacks.
        database_size = owner.execute(
            "SELECT pg_size_pretty(pg_database_size(current_database()))"
        ).fetchone()
        owner.execute("SET ROLE pw_owner")
        owner.execute("SET default_transaction_read_only = on")
        with owner.transaction():
            owner.execute("SELECT set_config('app.tenant_id', %s, true)", (str(TENANT),))
            for i, c in owner.execute(
                "SELECT id::text, code FROM pw.bin WHERE site_id = %s"
                " UNION ALL SELECT id::text, 'zone:' || code FROM pw.zone WHERE site_id = %s",
                (SITE, SITE),
            ).fetchall():
                codes[i] = c
            volume = dict(
                owner.execute(
                    """
                    SELECT 'movements', count(*) FROM pw.movement WHERE site_id = %(s)s
                    UNION ALL SELECT 'order lines', count(*) FROM pw.order_line WHERE site_id = %(s)s
                    UNION ALL SELECT 'orders', count(*) FROM pw.outbound_order WHERE site_id = %(s)s
                    UNION ALL SELECT 'balances', count(*) FROM pw.inventory_balance WHERE site_id = %(s)s
                    UNION ALL SELECT 'bins', count(*) FROM pw.bin WHERE site_id = %(s)s
                    UNION ALL SELECT 'products', count(*) FROM pw.product
                    UNION ALL SELECT 'days of history',
                      (SELECT extract(day FROM max(occurred_at) - min(occurred_at))::bigint
                         FROM pw.movement WHERE site_id = %(s)s)
                    """,
                    {"s": SITE},
                ).fetchall()
            )

        for row, (_, _, fn, ref) in zip(results, calls, strict=True):
            if not ref or row["verdict"] == "error":
                continue
            with tenant_session(read, TENANT, read_snapshot=True) as conn:
                envelope = fn(conn)
            got = sorted({codes.get(str(h.target_id), str(h.target_id)) for h in envelope.highlights})
            gold = run_reference(owner, TENANT, SITE, ref)
            row["highlights"] = len(got)
            row["matches_reference"] = got == gold or (
                envelope.highlights_truncated and set(got) <= set(gold)
            )

    # NFR-PERF-06: the agent's read sessions carry a statement timeout.
    with tenant_session(read, TENANT, read_snapshot=True) as conn:
        timeout_row = conn.execute("SHOW statement_timeout").fetchone()
    timeout = str(timeout_row[0]) if timeout_row else "unknown"

    report = {
        "date": time.strftime("%Y-%m-%d"),
        "git_sha": git_sha(),
        "machine": _machine(),
        "runs_per_call": args.runs,
        "volume": {k: int(v) for k, v in volume.items()},
        "database_size": database_size[0] if database_size else None,
        "statement_timeout": timeout,
        "memory": _docker_memory(),
        "calls": results,
    }
    failing = [r["call"] for r in results if r["verdict"] != "pass" or r.get("matches_reference") is False]
    report["status"] = "pass" if not failing else "fail: " + ", ".join(failing)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    base = args.report_dir / f"{report['date']}-{report['git_sha']}-semantic-180d"
    base.with_suffix(".json").write_text(json.dumps(report, indent=1, default=str) + "\n")
    base.with_suffix(".md").write_text(_markdown(report))
    sys.stdout.write(f"\n{report['status']}\nReport: {base.with_suffix('.md')}\n")
    return 0 if not failing else 1


def _markdown(r: dict[str, Any]) -> str:
    m = r["machine"]
    lines = [
        "# Semantic layer performance at full volume (Slice 2 step 7d)",
        "",
        f"Status: **{r['status']}**. Commit {r['git_sha']}, {r['date']}, {r['runs_per_call']} runs per "
        "call after 3 warm-ups, the agent's read role inside a read-only snapshot transaction (the tools' "
        "data path).",
        "",
        f"Machine (NFR-PERF-07): {m['model']}, {m['cpu']}, {m['cores']} cores, {m['memory_gb']} GB, "
        f"{m['os']}, Python {m['python']}; the database in Docker Desktop on the same machine.",
        "",
        "| Volume | Rows |",
        "|---|---|",
        *[f"| {k} | {v:,} |" for k, v in r["volume"].items()],
        f"| database size | {r['database_size']} |",
        "",
        f"Statement timeout on the agent's read sessions (NFR-PERF-06): `{r['statement_timeout']}`.",
        "",
        "| Call | Budget p95 | p50 | p95 | max | Rows | Verdict | Highlights match reference |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c in r["calls"]:
        match = c.get("matches_reference")
        lines.append(
            f"| {c['call']} | {c['budget_ms']:.0f} ms | {c.get('p50_ms', '-')} | {c.get('p95_ms', '-')} | "
            f"{c.get('max_ms', '-')} | {c.get('rows', '-')} | {c['verdict']}"
            + (f" ({c['error']})" if c.get("error") else "")
            + f" | {'n/a' if match is None else ('yes' if match else 'NO')} |"
        )
    if r["memory"]:
        lines += ["", "| Container | Memory, CPU at the end of the run |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in sorted(r["memory"].items())]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())

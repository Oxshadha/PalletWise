"""CLI: python -m evals --set core --set adversarial --level deterministic|model [--write-golden].

Model level: --config NAME runs one configuration; --compare A,B runs several on the same cases,
interleaved (ADR 0001). --compare baseline,baseline is an A/A run that measures run-to-run noise."""

import argparse
import logging
import sys
from pathlib import Path

from evals.cases import load_set
from evals.configs import named
from evals.environment import standalone
from evals.measure import BRIEF_GATES, check_gates, combined_status
from evals.runner import (
    REPORTS_DIR,
    TOOL_NAMES,
    ConfigRun,
    brief_wording,
    compare,
    config_names,
    deterministic,
    git_sha,
    write_golden,
    write_model_report,
    write_report,
)
from palletwise.agent.runtime import choose_route
from palletwise.llm import DisabledProvider, build_provider
from palletwise.settings import LlmSettings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals")
    parser.add_argument("--set", action="append", dest="sets", default=None)
    parser.add_argument("--level", choices=["deterministic", "model"], default="deterministic")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--write-golden", action="store_true")
    parser.add_argument("--report-dir", type=Path, default=REPORTS_DIR)
    parser.add_argument("--config", default="production", help="one named runtime configuration")
    parser.add_argument("--compare", default=None, help="model level: named configurations, comma separated")
    parser.add_argument(
        "--tier3-only",
        action="store_true",
        help="model level: only the cases the three-tier runtime leaves to the model (tier 3)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)

    sha = git_sha()  # at the start: a commit made during a long run must not relabel it
    names = args.sets or ["core", "adversarial", "operations", "routing"]
    cases, hashes = [], {}
    for name in names:
        found, digest = load_set(name)
        cases += found
        hashes[name] = digest

    provider = build_provider(LlmSettings()) if args.level == "model" else DisabledProvider()
    if args.level == "model" and isinstance(provider, DisabledProvider):
        # Never skipped or passed silently (evals spec 5).
        paths = write_report(
            "model", hashes, None, [], status="pending, provider unavailable", directory=args.report_dir
        )
        print(f"model gate: PENDING (provider unavailable). Report: {paths[1]}")
        return 3

    every_case = list(cases)
    if args.tier3_only:
        cases = [
            c
            for c in cases
            if choose_route(c.question, None, c.page_context, TOOL_NAMES, semantic=True)[0] is None
        ]
        hashes["selection"] = "tier3-only"

    with standalone() as env:
        if args.write_golden:
            for path in write_golden(env, cases):
                print(f"golden: {path}")
        if args.level == "deterministic":
            results = deterministic(env, cases, named([args.config])[0])
            failed = [r for r in results if not r.passed]
            status = "pass" if not failed else "fail"
            paths = write_report(
                "deterministic", hashes, env, results, status=status, directory=args.report_dir, sha=sha
            )
            print(
                f"deterministic gate: {status.upper()}, {len(results) - len(failed)} of {len(results)}"
                f" cases. Report: {paths[1]}"
            )
            return 0 if not failed else 1
        llm = LlmSettings()
        configs = named(args.compare.split(",") if args.compare else [args.config])
        # A provider per configuration: each API runs on its own event loop (runner.compare).
        compared = compare(
            env, cases, lambda: build_provider(LlmSettings()), configs, runs=args.runs, lookup=every_case
        )
        entries = [
            ConfigRun(name, config.as_json(), results)
            for (name, results), config in zip(compared, configs, strict=True)
        ]
        # The daily brief, once: it is stored per site and day, so every configuration reads it.
        brief = brief_wording(env, lambda: build_provider(LlmSettings()))
        for entry in entries:
            entry.metrics.update(brief)
            entry.gates += check_gates(entry.metrics, BRIEF_GATES)
            invalid = entry.status.split("; ")[0] + "; " if entry.status.startswith("invalid") else ""
            entry.status = invalid + combined_status(entry.gates)
        paths = write_model_report(
            hashes,
            env,
            entries,
            model_info={"models": llm.models, "location": llm.location, "runs": args.runs},
            directory=args.report_dir,
            sha=sha,
        )
        for name, entry in zip(config_names(configs), entries, strict=True):
            print(f"model gate [{name}]: {entry.status.upper()}")
        print(f"Report: {paths[1]}")
        return 0 if all(e.status.startswith("pass") for e in entries) else 1


if __name__ == "__main__":
    sys.exit(main())

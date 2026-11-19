"""CLI: python -m evals --set core --set adversarial --level deterministic|model [--write-golden]."""

import argparse
import logging
import sys
from pathlib import Path

from evals.cases import load_set
from evals.environment import standalone
from evals.runner import GATES, REPORTS_DIR, deterministic, model, score, write_golden, write_report
from palletwise.llm import DisabledProvider, build_provider
from palletwise.settings import LlmSettings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals")
    parser.add_argument("--set", action="append", dest="sets", default=None)
    parser.add_argument("--level", choices=["deterministic", "model"], default="deterministic")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--write-golden", action="store_true")
    parser.add_argument("--report-dir", type=Path, default=REPORTS_DIR)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)

    names = args.sets or ["core", "adversarial"]
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

    with standalone() as env:
        if args.write_golden:
            for path in write_golden(env, cases):
                print(f"golden: {path}")
        if args.level == "deterministic":
            results = deterministic(env, cases)
            failed = [r for r in results if not r.passed]
            status = "pass" if not failed else "fail"
            paths = write_report(
                "deterministic", hashes, env, results, status=status, directory=args.report_dir
            )
            print(
                f"deterministic gate: {status.upper()}, {len(results) - len(failed)} of {len(results)}"
                f" cases. Report: {paths[1]}"
            )
            return 0 if not failed else 1
        llm = LlmSettings()
        results = model(env, cases, provider, runs=args.runs)
        metrics = score(results)
        gated = {k: v for k, v in metrics.items() if k in GATES}
        failing = [k for k, v in gated.items() if v is not None and v < GATES[k]]
        status = "pass" if not failing else f"fail: {', '.join(failing)}"
        paths = write_report(
            "model",
            hashes,
            env,
            results,
            status=status,
            metrics=metrics,
            model_info={"models": llm.models, "location": llm.location, "runs": args.runs},
            directory=args.report_dir,
        )
        print(f"model gate: {status.upper()}. Report: {paths[1]}")
        return 0 if not failing else 1


if __name__ == "__main__":
    sys.exit(main())

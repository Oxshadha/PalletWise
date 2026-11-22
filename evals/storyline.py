"""The storyline test (slice-4-acceptance step 6): the guided demo, clicked through on the live stack.

Signs in as the dev Manager and drives the real screen: every step of `storyline.json`, its setup
through the time bar's confirmation, every prompt chip through the copilot. Each run starts with a
reset through the screen, so a run is the demo exactly as a presenter gives it, and two runs must
give the same results (Definition of done). Nothing is written to the database except through the
product: reset, time shifts, scenarios and questions.

Measured per shift (NFR-PERF-05, step 6 limits): the total from confirming until the bar shows the
result, and the screen latency from the moment the API reports the shift applied until the bar
shows it. A 1-day shift is measured after the storyline.

    uv run --with playwright python -m evals.storyline [--runs 2] [--out DIR]

Reads the dev users' passwords from `.env`; never prints them. Needs the stack (`make up`) and the
copilot's model credentials, as the demo does.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

from palletwise.agent.runtime import NO_MODEL_TEXT

REPO = Path(__file__).resolve().parents[1]
STORYLINE = REPO / "apps/web/src/features/simulation/storyline.json"
LIMITS = {"1d": 30.0, "1w": 180.0}  # seconds, fully applied with the screen updated
SCREEN_LATENCY = 2.0  # seconds, an applied change on the screen (NFR-PERF-05)
REBUILD_TIMEOUT = 900.0
ANSWER_TIMEOUT = 120.0
# A demo question answered only with the copilot's limited-mode text has not been answered.
LIMITED = NO_MODEL_TEXT.split(".")[0]
EMPTY_SETTLES = 7.0  # seconds: longer than the evaluator's reconcile interval


def env() -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in (REPO / ".env").read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    )


class Demo:
    def __init__(self, page: Page, base: str, out: Path) -> None:
        self.page, self.base, self.out = page, base, out
        me = page.request.get(f"{base}/api/me").json()
        self.site = me["sites"][0]["id"]

    def state(self) -> dict[str, Any]:
        return dict(self.page.request.get(f"{self.base}/api/sites/{self.site}/simulation").json())

    def shot(self, name: str) -> None:
        self.page.screenshot(path=str(self.out / f"{name}.png"))

    def recalculated(self) -> str:
        """'current' when the engine has evaluated the inbox's moment (one transaction updates every
        open exception's last_evaluated_at), 'empty' when there is nothing to tell, else 'behind'."""
        response = self.page.request.get(f"{self.base}/api/sites/{self.site}/exceptions")
        if not response.ok:
            return "behind"
        inbox = response.json()
        latest = max((e["last_evaluated_at"] for e in inbox["exceptions"]), default=None)
        if latest is None:
            return "empty"
        return "current" if latest == inbox["as_of"] else "behind"

    def wait_recalculated(self, timeout: float = 60.0) -> None:
        """Until the engine has evaluated the current moment. An empty inbox says nothing (the
        first evaluation after a reset may not have run): it must stay empty for longer than the
        evaluator's 5-second cycle."""
        deadline = time.monotonic() + timeout
        empty_since: float | None = None
        while True:
            state = self.recalculated()
            if state == "current":
                return
            if state == "empty":
                empty_since = empty_since or time.monotonic()
                if time.monotonic() - empty_since > EMPTY_SETTLES:
                    return
            else:
                empty_since = None
            if time.monotonic() > deadline:
                raise TimeoutError("the exceptions were not recalculated in time")
            self.page.wait_for_timeout(250)

    def bar_time(self) -> str:
        return self.page.locator(".pw-timebar-time strong").inner_text()

    def confirm(self, label: str) -> str | None:
        """Confirms the pending action; returns the site time just before it was sent."""
        before = self.state().get("site_time")
        self.page.get_by_role("alertdialog").get_by_role("button", name=label).click()
        return before

    def settle(self, before: str | None, timeout: float) -> dict[str, float]:
        """After a confirmed action: until the bar's progress is gone. Returns the total and the
        screen latency (API applied to bar updated)."""
        started = time.monotonic()
        applied_at: float | None = None
        shown_at: float | None = None
        while time.monotonic() - started < timeout:
            busy = self.page.locator(".pw-timebar-progress").count() > 0
            if applied_at is None:
                now = self.state()
                if now["data_status"] == "READY" and not now["shifting"] and now["site_time"] != before:
                    applied_at = time.monotonic()
            if not busy and time.monotonic() - started > 0.5:
                shown_at = time.monotonic()
                if applied_at is not None:
                    break
            self.page.wait_for_timeout(100)
        if shown_at is None:
            raise TimeoutError(f"the bar did not settle within {timeout:.0f} s")
        # The screens are up to date once the exceptions follow the new time too.
        self.wait_recalculated()
        recalculated_at = time.monotonic()
        return {
            "total_s": round(shown_at - started, 2),
            "screen_latency_s": round(max(0.0, shown_at - (applied_at or shown_at)), 2),
            "with_exceptions_s": round(recalculated_at - started, 2),
        }

    def open_bar(self) -> None:
        """The time controls start folded to a pill beside the site name; open them."""
        pill = self.page.locator(".pw-timebar-pill")
        if pill.count() > 0:
            pill.click()

    def reset(self) -> float:
        started = time.monotonic()
        self.open_bar()
        self.page.get_by_role("button", name="Reset", exact=True).click()
        self.confirm("Reset the simulation")
        self.page.get_by_role("status").filter(has_text="Resetting").wait_for(timeout=30000)
        deadline = time.monotonic() + REBUILD_TIMEOUT
        while self.page.locator(".pw-timebar-progress").count() > 0:
            if time.monotonic() > deadline:
                raise TimeoutError("the rebuild did not finish")
            self.page.wait_for_timeout(1000)
        self.wait_recalculated()
        return round(time.monotonic() - started, 1)

    def inbox_count(self) -> str:
        self.page.get_by_role("button", name="Inbox", exact=True).click()
        header = self.page.locator(".pw-inbox [role=status]").first
        header.wait_for(timeout=20000)
        self.page.wait_for_timeout(1500)  # the list follows the stream's last change
        text = header.inner_text()
        self.page.get_by_role("button", name="Warehouse", exact=True).click()
        return text

    def ask(self, prompt: str) -> dict[str, Any]:
        turns = self.page.locator(".pw-turn")
        before = turns.count()
        self.page.locator(".pw-demo").get_by_role("button", name=prompt, exact=True).click()
        deadline = time.monotonic() + ANSWER_TIMEOUT
        while time.monotonic() < deadline:
            if turns.count() > before and turns.last.get_attribute("aria-busy") == "false":
                break
            self.page.wait_for_timeout(250)
        turn = turns.last
        error = turn.locator(".pw-answer-error")
        text = turn.locator(".pw-answer-text").inner_text() if turn.locator(".pw-answer-text").count() else ""
        return {
            "prompt": prompt,
            "answered": turns.count() > before and turn.get_attribute("aria-busy") == "false",
            "error": error.inner_text() if error.count() else None,
            "highlights": self.page.locator(".pw-locations .pw-location-code").count(),
            "answer": " ".join(text.split())[:200],
        }


def run_once(page: Page, base: str, out: Path, label: str) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    demo = Demo(page, base, out)
    result: dict[str, Any] = {"label": label, "reset_s": demo.reset(), "steps": [], "shifts": []}
    demo.shot("00-after-reset")
    steps = json.loads(STORYLINE.read_text(encoding="utf-8"))["steps"]
    page.get_by_role("button", name="Demo", exact=True).click()
    for i, step in enumerate(steps):
        heading = page.locator("#pw-demo-title")
        if heading.inner_text() != step["title"]:
            raise AssertionError(f"step {i + 1}: the panel shows {heading.inner_text()!r}")
        record: dict[str, Any] = {"step": i + 1, "title": step["title"]}
        setup = step.get("setup")
        if setup:
            page.locator(".pw-demo").get_by_role("button", name=setup["label"]).click()
            before = demo.confirm("Move time" if setup["type"] == "shift" else setup["label"])
            timing = demo.settle(before, LIMITS["1w"] + 60)
            record["setup"] = {**setup, **timing}
            if setup["type"] == "shift":
                result["shifts"].append({"by": setup["by"], **timing})
        record["site_time"] = demo.bar_time()
        record["inbox"] = demo.inbox_count()
        record["answers"] = [demo.ask(p) for p in step["prompts"]]
        demo.shot(f"{i + 1:02d}-{step['id']}")
        result["steps"].append(record)
        print(f"  {label} step {i + 1} {step['title']}: {record['site_time']}; {record['inbox']}", flush=True)
        if i < len(steps) - 1:
            page.get_by_role("button", name="Next", exact=True).click()
    page.get_by_role("button", name="Close the demo").click()

    # A 1-day shift, measured on its own.
    page.get_by_role("button", name="+1 day").click()
    before = demo.confirm("Move time")
    result["shifts"].append({"by": "1d", **demo.settle(before, LIMITS["1d"] + 60)})
    dashes = page.evaluate("document.body.innerText.match(/[\\u2013\\u2014]/g)")
    result["dashes_on_screen"] = dashes or []
    return result


def comparable(run: dict[str, Any]) -> list[Any]:
    """What two runs from the same seed must agree on (the model's wording may differ)."""
    return [
        (s["site_time"], s["inbox"], [(a["answered"], a["error"] is None) for a in s["answers"]])
        for s in run["steps"]
    ]


def verdict(runs: list[dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    for run in runs:
        for shift in run["shifts"]:
            limit = LIMITS.get(shift["by"])
            if limit is not None and shift["with_exceptions_s"] > limit:
                problems.append(
                    f"{run['label']}: +{shift['by']} took {shift['with_exceptions_s']} s (limit {limit} s)"
                )
            if shift["screen_latency_s"] > SCREEN_LATENCY:
                problems.append(
                    f"{run['label']}: +{shift['by']} reached the screen {shift['screen_latency_s']} s late"
                )
        for step in run["steps"]:
            for answer in step["answers"]:
                limited = answer["answer"].startswith(LIMITED)
                if not answer["answered"] or answer["error"] or limited:
                    said = answer["error"] or answer["answer"]
                    problems.append(f"{run['label']} step {step['step']}: {answer['prompt']!r} gave {said}")
        if run["dashes_on_screen"]:
            problems.append(f"{run['label']}: dashes on screen")
    if len(runs) > 1 and any(comparable(r) != comparable(runs[0]) for r in runs[1:]):
        problems.append("the runs differ (site times, inbox counts or answer outcomes)")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.storyline")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--out", type=Path, default=REPO / "docs/quality/storyline")
    args = parser.parse_args(argv)
    secrets = env()
    runs: list[dict[str, Any]] = []
    with sync_playwright() as pw:
        # The 2D plan: headless 3D would measure software rendering, not the product.
        browser = pw.chromium.launch(args=["--disable-webgl", "--disable-3d-apis"])
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.goto(args.base + "/")
        page.wait_for_selector("#username", timeout=30000)
        page.fill("#username", "manager")
        page.fill("#password", secrets["KC_DEV_MANAGER_PASSWORD"])
        page.click("#kc-login")
        page.get_by_role("region", name="Simulation time").wait_for(timeout=60000)
        for n in range(args.runs):
            label = f"run {n + 1}"
            print(f"{label}: reset, then the storyline", flush=True)
            runs.append(run_once(page, args.base, args.out / f"run-{n + 1}", label))
        browser.close()
    problems = verdict(runs)
    report = {"runs": runs, "problems": problems, "passed": not problems}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for run in runs:
        for shift in run["shifts"]:
            print(
                f"{run['label']}: +{shift['by']} {shift['total_s']} s, screen {shift['screen_latency_s']} s,"
                f" with exceptions {shift['with_exceptions_s']} s"
            )
    print("PASS" if not problems else "FAIL\n" + "\n".join(problems))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())

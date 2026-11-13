# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fail closed unless every pinned readiness test runs and passes."""

import runpy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
READINESS = "tests/test_mcp_readiness.py"


class EvidenceGate:
    def __init__(self, expected):
        self.expected = set(expected)
        self.passed = set()
        self.invalid = set()

    def pytest_collection_finish(self, session):
        selected = {item.nodeid for item in session.items}
        missing = self.expected - selected
        marked = {
            item.nodeid
            for item in session.items
            if item.nodeid in self.expected
            and any(
                next(item.iter_markers(name), None) is not None
                for name in ("skip", "skipif", "xfail")
            )
        }
        if missing or marked:
            raise pytest.UsageError(
                f"MCP readiness evidence missing/deselected: {sorted(missing)}; "
                f"skip/xfail markers: {sorted(marked)}"
            )

    def pytest_runtest_logreport(self, report):
        if report.nodeid not in self.expected:
            return
        if report.outcome != "passed" or hasattr(report, "wasxfail"):
            self.invalid.add(report.nodeid)
        elif report.when == "call":
            self.passed.add(report.nodeid)

    def pytest_sessionfinish(self, session, exitstatus):
        if not session.config.option.collectonly:
            missing = self.expected - self.passed
            if missing or self.invalid:
                reporter = session.config.pluginmanager.get_plugin("terminalreporter")
                if reporter:
                    reporter.write_line(
                        "MCP readiness requires every test to pass: "
                        + str(sorted(missing | self.invalid)),
                        red=True,
                    )
                session.exitstatus = pytest.ExitCode.TESTS_FAILED


def main():
    evidence = runpy.run_path(str(ROOT / READINESS))
    pins = evidence["PINNED_IDS"]
    direct = [f"{READINESS}::{name}" for name in evidence if name.startswith("test_")]
    return pytest.main(
        [
            "-q",
            "-o",
            "addopts=",
            "-p",
            "no:randomly",
            "-p",
            "no:cacheprovider",
            *pins,
            *direct,
            *sys.argv[1:],
        ],
        plugins=[EvidenceGate((*pins, *direct))],
    )


if __name__ == "__main__":
    raise SystemExit(main())

import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import workbench


class RunnerTests(unittest.TestCase):
    def test_unrelated_exception_is_not_reproduction(self):
        record = {"exception": {"type": "ModuleNotFoundError", "message": "missing"}}
        for case in workbench.CASES:
            self.assertEqual(workbench.classify(case, record, ""), "inconclusive")

    def test_wrong_routing_failure_is_not_reproduction(self):
        record = {"exception": {"type": "AssertionError", "message": ""}}
        self.assertEqual(workbench.classify("werkzeug-2834", record,
                                          "Observed status: 500"), "inconclusive")

    def test_timeout_preserves_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            result = workbench.execute(
                [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(10)"],
                directory, "test", 0.5, os.environ.copy())
            self.assertEqual(result["status"], "timed_out")
            self.assertIn("started", (directory / "test.stdout.txt").read_text())

    def test_setup_failure_is_saved_as_environment_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            args = SimpleNamespace(output=Path(folder), setup_timeout=10, timeout=10,
                                   wheelhouse=None, keep_env=False)
            failure = {"status": "completed", "exit_code": 1, "duration_seconds": 0,
                       "command": ["fake-venv-command"]}
            with patch.object(workbench, "execute", return_value=failure):
                self.assertFalse(workbench.run_case("werkzeug-2842", "buggy", args))
            report = json.loads(next(Path(folder).glob("*/result.json")).read_text())
            self.assertEqual(report["outcome"], "environment_failure")
            self.assertFalse(report["matches_expectation"])


if __name__ == "__main__":
    unittest.main()

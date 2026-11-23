"""Offline checks for weekly recovery; never dispatch real workflows."""
from datetime import datetime, timezone
import unittest
from unittest.mock import Mock, patch

import weekly_fallback as W


def run(run_id=1, status="completed", conclusion="success"):
    return {"id": run_id, "status": status, "conclusion": conclusion,
            "event": "workflow_dispatch", "html_url": f"https://example.com/runs/{run_id}"}


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.start = W.day_start(datetime(2026, 11, 22, 23, tzinfo=timezone.utc))

    def test_beijing_monday_start_is_sunday_utc(self):
        self.assertEqual(self.start.astimezone(timezone.utc).isoformat(),
                         "2026-11-22T16:00:00+00:00")

    def test_success_skips_dispatch(self):
        api = Mock()
        api.runs.return_value = [run()]
        api.report_completed.return_value = True
        W.recover(api, self.start, 99)
        api.request.assert_not_called()

    def test_missing_or_failed_report_dispatches(self):
        for history in ([], [run(conclusion="failure")]):
            api = Mock()
            api.runs.side_effect = [history, history, [run(2)]]
            api.report_completed.return_value = False
            W.recover(api, self.start, 99)
            api.request.assert_called_once_with(
                "/actions/workflows/weekly.yml/dispatches",
                {"ref": "main", "inputs": {"automatic": True}})

    @patch.object(W.time, "sleep")
    def test_waits_for_running_report_then_skips(self, sleep):
        api = Mock()
        api.runs.side_effect = [[run(status="in_progress")],
                               [run(status="in_progress")], [run()]]
        api.report_completed.return_value = True
        W.recover(api, self.start, 99)
        sleep.assert_called_once_with(30)
        api.request.assert_not_called()

    def test_api_failure_does_not_dispatch(self):
        api = Mock()
        api.runs.side_effect = OSError("API unavailable")
        with self.assertRaises(OSError):
            W.recover(api, self.start, 99)
        api.request.assert_not_called()

    def test_excludes_self_and_allows_guard_after_failed_run(self):
        api = Mock()
        api.runs.return_value = [run(99), run(1, conclusion="failure")]
        api.report_completed.return_value = False
        self.assertEqual(W.inspect_runs(api, self.start, 99), (False, []))
        api.report_completed.assert_called_once()

    def test_green_workflow_with_skipped_report_does_not_count(self):
        api = W.GitHub.__new__(W.GitHub)
        api.pages = Mock(return_value=iter([
            {"name": "check", "conclusion": "success"},
            {"name": "report", "conclusion": "skipped"},
        ]))
        self.assertFalse(api.report_completed(run()))

    def test_run_pagination(self):
        api = W.GitHub.__new__(W.GitHub)
        api.request = Mock(side_effect=[{"workflow_runs": [run()] * 100},
                                        {"workflow_runs": [run(2)]}])
        self.assertEqual(len(api.runs(self.start)), 101)
        self.assertIn("page=2", api.request.call_args.args[0])


if __name__ == "__main__":
    unittest.main()

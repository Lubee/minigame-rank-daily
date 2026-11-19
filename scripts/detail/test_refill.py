"""Scoped regression checks; all writes use temporary directories."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import merge_details
import refill


class RefillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in {"ROOT": root, "DETAIL_DIR": root, "INDEX": root / "index.json",
                            "WORKLIST": root / "worklist.json", "STATE": root / "state.json"}.items():
            self.stack.enter_context(patch.object(refill, name, value))
        self.output = io.StringIO()
        self.stack.enter_context(contextlib.redirect_stdout(self.output))

    def write(self, name, data):
        path = refill.ROOT / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_actual_file_overrides_stale_worklist_status(self):
        self.write("index.json", {"games": {"A": {"slug": "a"}, "B": {"slug": "b"}}})
        self.write("a.json", {})
        result = refill.worklist_pending([{"name": "A", "status": "pending"},
                                          {"name": "B", "status": "done"}])
        self.assertEqual([item["name"] for item in result], ["B"])

    def test_latest_then_best_rank_before_limit(self):
        self.write("worklist.json", {"entries": [{"name": "old", "best_rank": 1},
                    {"name": "new2", "best_rank": 9}, {"name": "new1", "best_rank": 3}]})
        self.write("data/latest.json", {"platforms": {"ios": {"boards": [
            {"rows": [{"name": "new1"}, {"name": "new2"}]}]}}})
        args = SimpleNamespace(scope="all", names_list=[], limit=2)
        self.assertEqual([x["name"] for x in refill.selected_targets(args)], ["new1", "new2"])

    def test_media_only_receives_selected_names(self):
        with patch.object(refill, "run_script", return_value=(0, "")) as run:
            refill.fetch_media(SimpleNamespace(force_media=False), [{"name": "A"}])
        self.assertEqual(json.loads(run.call_args.args[1][1]), ["A"])

    def test_explicit_merge_inputs_exclude_other_batches(self):
        current = self.write("batch_1.json", [{"name": "A"}])
        self.write("batch_2.json", [{"name": "unrelated"}])
        with patch.object(merge_details, "STAGING_DIR", refill.ROOT):
            self.assertEqual([x["name"] for x in merge_details.load_staging([current])], ["A"])

    def test_partial_finish_keeps_missing_targets_and_scopes_merge(self):
        item = {"name": "A", "tech": {"engine": "unknown", "dims": dict.fromkeys(
            ["engine", "art", "anim", "physics", "logic", "data", "server", "audio"], 2)},
            "play": {"core_loop": "test loop", "highlights": ["test"]},
            "clone": {"verdict": "不建议"}, "sources": ["https://example.com"],
            "biz": {"internal": {"fit": "需改造", "why": [{"lens": lens, "text": "test"}
                for lens in ["用户", "用户", "业务", "业务"]], "how": ["a", "b"],
                "gain": ["a", "b", "c", "d"]},
                "opportunity": {"fit": "可观望", "points": ["test"]},
                "extension": {"scenes": [{"scene": "test", "how": "test"}]}}}
        self.write("batch_1.json", [item])
        state = {"targets": ["A", "B"], "errors": {"B": "needs more sources"}, "batches": [
            {"names": ["A"], "output": "batch_1.json"},
            {"names": ["B"], "output": "batch_2.json"}]}
        self.write("state.json", state)
        with patch.object(refill, "run_script", return_value=(0, "")) as run, \
             patch.object(refill, "archived_keys", return_value={"A"}):
            self.assertEqual(refill.finish(SimpleNamespace(force=False, scope="all")), 3, self.output.getvalue())
        self.assertEqual(run.call_args_list[0].args[1], ["--input", "batch_1.json"])
        self.assertEqual(refill.read_state()["pending"], ["B"])
        self.assertEqual(refill.read_state()["errors"], {"B": "needs more sources"})
        daily = refill.read_state()["daily_completed"]
        self.assertEqual(next(iter(daily.values())), ["A"])
        refill.save_state([{"name": "B"}], [["B"]], 3, 5, "all")
        self.assertEqual(refill.read_state()["daily_completed"], daily)

    def test_out_of_batch_patch_rejected_without_merging(self):
        self.write("batch_1.json", [{"name": "unrelated", "biz": {}}])
        self.write("state.json", {"targets": ["A"], "batches": [
            {"names": ["A"], "output": "batch_1.json"}]})
        with patch.object(refill, "run_script") as run:
            self.assertEqual(refill.finish(SimpleNamespace(force=False, scope="all")), 1)
            run.assert_not_called()
        self.assertTrue(refill.read_state()["errors"])

    def test_taptap_media_keeps_its_source(self):
        record = {}
        merge_details.merge_media(record, {"matched": True, "platform": "taptap",
            "icon": "https://example.com/icon", "screenshots": ["https://example.com/shot"]})
        self.assertEqual({shot["source"] for shot in record["shots"]}, {"TapTap"})

    def test_unfinished_state_resumes_without_fetch_or_recut(self):
        self.write("data/daily/2026-11-18.json", {})
        original = self.write("input.json", {"games": [{"name": "A"}]})
        before = original.read_bytes()
        self.write("state.json", {"targets": ["A"], "batches": [
            {"names": ["A"], "input": "input.json", "output": "missing.json"}]})
        with patch("sys.argv", ["refill.py", "--scope", "all", "--prepare-only"]), \
             patch.object(refill, "refresh_worklist", return_value=0), \
             patch.object(refill, "checkpoint", return_value=0), \
             patch.object(refill, "fetch_media") as fetch, \
             patch.object(refill, "cut_batches") as cut:
            self.assertEqual(refill.main(), 0)
            fetch.assert_not_called()
            cut.assert_not_called()
        self.assertEqual(original.read_bytes(), before)

    def test_insufficient_is_counted_once_per_round_and_stops_at_two(self):
        self.write("state.json", {"targets": ["A"]})
        self.assertEqual(refill.record_insufficient("A", "No reliable rules", "day1"), 0)
        refill.record_insufficient("A", "Same round retried", "day1")
        self.assertEqual(refill.stopped_keys(), set())
        refill.record_insufficient("A", "Still insufficient", "day2")
        self.assertEqual(refill.stopped_keys(), {"A"})
        history = refill.load_json(refill.DETAIL_DIR / "_research_failures.json")
        self.assertEqual(len(history["A"]["rounds"]), 2)
        self.assertEqual(history["A"]["rounds"]["day1"]["reason"], "No reliable rules")

    def test_stopped_games_are_not_selected_or_treated_as_completed(self):
        self.write("state.json", {"targets": ["A"]})
        refill.record_insufficient("A", "No reliable sources", "day1")
        refill.record_insufficient("A", "No reliable sources", "day2")
        entries = [{"name": "A"}, {"name": "B"}]
        self.assertEqual([e["name"] for e in refill.pick_targets(entries, "all", [], set())[0]], ["B"])
        self.assertFalse(refill.state_still_relevant(refill.read_state()))
        self.assertEqual(len(refill.worklist_pending(entries)), 2)
        self.assertNotIn("A", refill.archived_keys())

    def test_failures_cannot_be_recorded_for_unrelated_game(self):
        self.write("state.json", {"targets": ["A"]})
        self.assertEqual(refill.record_insufficient("B", "No sources", "day1"), 1)
        self.assertFalse((refill.DETAIL_DIR / "_research_failures.json").exists())

    def test_resume_fingerprints_reject_modified_and_unrelated_files(self):
        self.write("state.json", {"targets": ["A"], "batches": [
            {"input": "data/detail/_staging/input.json", "output": "data/detail/_staging/batch.json"}]})
        path = self.write("data/detail/_worklist.json", {"entries": []})
        with patch.object(refill.subprocess, "check_output", return_value=b' M data/detail/_worklist.json\0'):
            self.assertEqual(refill.checkpoint(), 0)
            self.assertEqual(refill.checkpoint(True), 0)
            path.write_text('{}', encoding='utf-8')
            self.assertEqual(refill.checkpoint(True), 1)
        with patch.object(refill.subprocess, "check_output", return_value=b' M README.md\0'):
            self.assertEqual(refill.checkpoint(), 1)


if __name__ == "__main__":
    unittest.main()

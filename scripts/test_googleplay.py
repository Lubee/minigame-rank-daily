import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import ci_diff
import ci_scrape
import scrape_googleplay as googleplay


def app(name="Test Game", package="com.example.game"):
    return {"title": name, "appId": package, "developer": "Test Studio"}


def fragment(name="Test Game"):
    return {"label": "Android", "boards": [{
        "label": "美区免费榜", "source": "google_play", "rows": [{
            "rank": 1, "name": name, "package": "com.example.game",
            "publisher": "Test Studio", "category": "Games",
        }],
    }]}


class GooglePlayTests(unittest.TestCase):
    def test_existing_free_board_is_preserved_when_only_new_chart_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "data/daily/2026-11-18.json"
            target.parent.mkdir(parents=True)
            original = fragment()
            target.write_text(json.dumps({"platforms": {"android": original}}), encoding="utf-8")
            new = fragment("New chart")
            new["boards"][0]["label"] = "美区免费新游榜"
            with patch.object(ci_scrape, "ROOT", root), patch.object(googleplay, "scrape", return_value=new) as scrape, \
                    patch("store_apps.update_store_apps"):
                ci_scrape.supplement_googleplay(target, lambda _: None)
            self.assertEqual(scrape.call_args.kwargs["charts"], ["new_free"])
            boards = json.loads(target.read_text(encoding="utf-8"))["platforms"]["android"]["boards"]
            self.assertEqual(boards[0], original["boards"][0])
            self.assertEqual(boards[1]["label"], "美区免费新游榜")

    def test_adapter_and_invalid_responses(self):
        responses = [
            [app()], [], [app(), app()], [app(package="invalid")],
            [app(name="")], [{"title": "Game"}], {"unexpected": True}, [None],
        ]
        for apps in responses:
            with self.subTest(apps=apps), patch("scrape_googleplay.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, json.dumps(apps), "")
                board = googleplay.scrape(top_n=100, log=lambda _: None)["boards"][0]
                if apps == [app()]:
                    self.assertEqual(board["rows"][0]["name"], "Test Game")
                    self.assertIn("id=com.example.game", board["rows"][0]["url"])
                    self.assertEqual(board["source"], "google_play")
                else:
                    self.assertEqual(board["rows"], [])
                    self.assertTrue(board["error"])

    def test_timeout_and_process_failure_are_reported(self):
        for error in (subprocess.TimeoutExpired("node", 45),
                      subprocess.CalledProcessError(1, "node", stderr="network failure")):
            with self.subTest(error=error), patch("scrape_googleplay.subprocess.run", side_effect=error):
                board = googleplay.scrape(log=lambda _: None)["boards"][0]
                self.assertEqual(board["rows"], [])
                self.assertTrue(board["error"])

    def test_only_missing_android_is_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            daily = root / "data/daily"
            daily.mkdir(parents=True)
            day = ci_scrape.datetime.now(ci_scrape.BEIJING_TZ).strftime("%Y-%m-%d")
            snapshot = {"date_beijing": day, "scraped_at_utc": "original", "platforms": {
                "wx": {"boards": [{"label": "人气榜", "rows": [{"name": "Keep"}]}]},
                "android": {"label": "Android", "boards": [
                    {"label": "美区免费榜", "rows": [], "error": "HTTP 429"}]},
            }}
            target = daily / f"{day}.json"
            latest = root / "data/latest.json"
            history = root / "data/history.jsonl"
            history_row = {"date": day, "boards": {"wx/人气榜": 1, "android/美区免费榜": 0}}
            for path in (target, latest):
                path.write_text(json.dumps(snapshot), encoding="utf-8")
            history.write_text(json.dumps(history_row) + "\n", encoding="utf-8")
            with patch.object(ci_scrape, "ROOT", root), patch.dict(os.environ, {}, clear=True), \
                    patch.object(ci_scrape, "resolve_auth", return_value=None), \
                    patch.object(ci_scrape, "scrape_gravity", side_effect=AssertionError("must not rescrape")), \
                    patch("store_apps.update_store_apps"), \
                    patch.object(googleplay, "scrape") as scrape:
                failed = copy.deepcopy(snapshot["platforms"]["android"])
                scrape.return_value = failed
                before = target.read_bytes()
                ci_scrape.main()
                self.assertEqual(target.read_bytes(), before)
                repaired = fragment()
                new_board = copy.deepcopy(repaired["boards"][0])
                new_board["label"] = "美区免费新游榜"
                repaired["boards"].append(new_board)
                scrape.return_value = repaired
                ci_scrape.main()
                result = json.loads(target.read_text(encoding="utf-8"))
                self.assertEqual(result["platforms"]["wx"], snapshot["platforms"]["wx"])
                self.assertEqual(result["scraped_at_utc"], "original")
                self.assertEqual(result, json.loads(latest.read_text(encoding="utf-8")))
                lines = history.read_text(encoding="utf-8").splitlines()
                self.assertEqual(len(lines), 1)
                self.assertEqual(json.loads(lines[0])["boards"]["android/美区免费榜"], 1)
                scrape.reset_mock()
                ci_scrape.main()
                scrape.assert_not_called()

    def test_first_new_source_is_baseline_and_next_day_has_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            daily, diff_dir = root / "daily", root / "diff"
            daily.mkdir()
            first = {"date_beijing": "2026-11-18", "platforms": {"android": fragment()}}
            (daily / "2026-11-18.json").write_text(json.dumps(first), encoding="utf-8")
            with patch.object(ci_diff, "DAILY", daily), patch.object(ci_diff, "DIFF", diff_dir), \
                    patch.dict(os.environ, {}, clear=True), patch.object(ci_diff.basemod, "save"):
                ci_diff.main()
                initial = json.loads((diff_dir / "2026-11-18.json").read_text(encoding="utf-8"))
                board = initial["boards"]["android/美区免费榜"]
                self.assertTrue(board["baseline"])
                self.assertEqual(board["totals"]["new_to_board"], 0)
                second = {"date_beijing": "2026-11-19", "platforms": {"android": fragment("New Game")}}
                (daily / "2026-11-19.json").write_text(json.dumps(second), encoding="utf-8")
                ci_diff.main()
                later = json.loads((diff_dir / "2026-11-19.json").read_text(encoding="utf-8"))
                self.assertEqual(later["boards"]["android/美区免费榜"]["totals"]["new_to_board"], 1)


if __name__ == "__main__":
    unittest.main()

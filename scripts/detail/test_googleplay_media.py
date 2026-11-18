"""Google Play identity and research material checks using isolated fixtures."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_worklist
import enrich_media
import scrape_googleplay


class GooglePlayMediaTests(unittest.TestCase):
    def test_package_is_in_worklist(self):
        with tempfile.TemporaryDirectory() as directory:
            daily = Path(directory)
            (daily / "2026-11-18.json").write_text(json.dumps({"platforms": {
                "android": {"boards": [{"label": "美区免费榜", "rows": [
                    {"name": "Example", "package": "com.example.game", "rank": 1}]}]}
            }}), encoding="utf-8")
            with patch.object(build_worklist, "DAILY_DIR", daily):
                result = build_worklist.collect_from_snapshots(None)
            self.assertEqual(result["Example"]["app_ids"]["android"], "com.example.game")

    def test_selected_android_gets_official_material_by_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            daily = root / "data/daily"
            daily.mkdir(parents=True)
            (daily / "2026-11-18.json").write_text(json.dumps({"platforms": {
                "android": {"boards": [{"label": "美区免费榜", "rows": [
                    {"name": "Example", "package": "com.example.game", "url": "https://play.google.com/store/apps/details?id=com.example.game"}]}]}
            }}), encoding="utf-8")
            app = {"appId": "com.example.game", "title": "Example", "developer": "Developer",
                   "description": "Official rules", "screenshots": ["https://play-lh.googleusercontent.com/image"],
                   "icon": "https://play-lh.googleusercontent.com/icon", "version": "1.2", "url": "https://play.google.com/store/apps/details?id=com.example.game"}
            with patch.object(enrich_media, "ROOT", root), \
                    patch.object(scrape_googleplay, "fetch_details", return_value=app) as fetch:
                media = enrich_media.identity_media({"name": "Example"})
            fetch.assert_called_once_with("com.example.game")
            self.assertEqual(media["platform"], "android")
            self.assertEqual(media["identity_match"], "package")
            self.assertEqual(media["description"], "Official rules")
            self.assertEqual(media["screenshots"], app["screenshots"])

    def test_detail_response_must_match_requested_package(self):
        with patch.object(scrape_googleplay.subprocess, "run") as run:
            run.return_value.stdout = json.dumps({"appId": "com.wrong.game", "title": "Example"})
            with self.assertRaises(ValueError):
                scrape_googleplay.fetch_details("com.example.game")

    def test_detail_failure_retains_network_reason(self):
        with patch.object(scrape_googleplay.subprocess, "run", side_effect=
                          subprocess.CalledProcessError(1, "node", stderr="TLS connection failed")):
            with self.assertRaisesRegex(ValueError, "TLS connection failed"):
                scrape_googleplay.fetch_details("com.example.game")

    def test_android_cache_and_identity_survive_network_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            daily = root / "data/daily"
            daily.mkdir(parents=True)
            base = root / "data/base"
            base.mkdir()
            (daily / "2026-11-18.json").write_text(json.dumps({"platforms": {
                "android": {"boards": [{"label": "美区免费榜", "rows": [
                    {"name": "Different display name", "package": "com.example.game"}]}]}
            }}), encoding="utf-8")
            record = {"name": "Example", "description": "Cached rules", "screenshots": ["https://example.com/shot"],
                      "details_checked_on": enrich_media.datetime.now(enrich_media.BEIJING).date().isoformat()}
            store = base / "store-apps.json"
            entry = {"name": "Example", "platform_app_ids": {"android": "com.example.game"}}
            for fresh in (True, False):
                if not fresh:
                    record["details_checked_on"] = "2020-01-01"
                store.write_text(json.dumps({"android:com.example.game": record}), encoding="utf-8")
                with self.subTest(fresh=fresh), patch.object(enrich_media, "ROOT", root), \
                        patch.object(scrape_googleplay, "fetch_details", side_effect=subprocess.TimeoutExpired("node", 45)) as fetch:
                    media = enrich_media.identity_media(entry)
                self.assertTrue(media["matched"])
                self.assertEqual(media["description"], "Cached rules")
                self.assertEqual(media["package"], "com.example.game")
                self.assertEqual(media["identity_match"], "package")
                self.assertEqual(fetch.call_count, 0 if fresh else 1)


if __name__ == "__main__":
    unittest.main()

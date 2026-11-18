"""Google Play metadata refresh stays bounded and preserves successful records."""
import copy
import subprocess
import unittest
from unittest.mock import patch

import store_apps


class GooglePlayStoreTests(unittest.TestCase):
    def test_complete_metadata_and_refresh_limit(self):
        apps = {f"android:com.example.g{i}": {"id": f"com.example.g{i}", "url": "https://play.google.com"}
                for i in range(15)}
        def details(package):
            return {"appId": package, "title": "Example", "developer": "Developer",
                    "description": "Official rules", "version": "2.0", "screenshots": ["https://play-lh.googleusercontent.com/shot"]}
        with patch.object(store_apps, "fetch_googleplay_details", side_effect=details) as fetch:
            store_apps.refresh_googleplay(apps, list(apps), "2026-11-18", lambda _: None)
        self.assertEqual(fetch.call_count, 12)
        for app in list(apps.values())[:12]:
            self.assertEqual(app["description"], "Official rules")
            self.assertEqual(app["developer"], "Developer")
            self.assertEqual(app["details_checked_on"], "2026-11-18")
        self.assertNotIn("details_checked_on", list(apps.values())[12])

    def test_failed_refresh_keeps_cached_data_and_retry_date(self):
        apps = {"android:com.example.game": {"id": "com.example.game", "description": "Keep",
                    "details_checked_on": "2026-11-01"}}
        before = copy.deepcopy(apps)
        with patch.object(store_apps, "fetch_googleplay_details", side_effect=subprocess.TimeoutExpired("node", 45)):
            store_apps.refresh_googleplay(apps, list(apps), "2026-11-18", lambda _: None)
        self.assertEqual(apps, before)


if __name__ == "__main__":
    unittest.main()

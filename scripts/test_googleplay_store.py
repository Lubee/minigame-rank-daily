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
                    "released": "Nov 14, 2012", "updated": 1576868577000,
                    "score": 4.6, "ratings": 1200, "reviews": 0, "installs": "100,000+",
                    "previewVideo": "https://play-games.googleusercontent.com/vp/mp4/example.mp4",
                    "description": "Official rules", "version": "2.0", "screenshots": ["https://play-lh.googleusercontent.com/shot"]}
        with patch.object(store_apps, "fetch_googleplay_details", side_effect=details) as fetch:
            store_apps.refresh_googleplay(apps, list(apps), "2026-11-18", lambda _: None)
        self.assertEqual(fetch.call_count, 12)
        for app in list(apps.values())[:12]:
            self.assertEqual(app["description"], "Official rules")
            self.assertEqual(app["developer"], "Developer")
            self.assertEqual(app["rating"], 4.6)
            self.assertEqual(app["rating_count"], 1200)
            self.assertEqual(app["review_count"], 0)
            self.assertEqual(app["installs"], "100,000+")
            self.assertEqual(app['videos'][0]['type'], 'mp4')
            self.assertEqual(app["details_checked_on"], "2026-11-18")
        self.assertNotIn("details_checked_on", list(apps.values())[12])

    def test_failed_refresh_keeps_cached_data_and_retry_date(self):
        apps = {"android:com.example.game": {"id": "com.example.game", "description": "Keep",
                    "details_checked_on": "2026-11-01"}}
        before = copy.deepcopy(apps)
        with patch.object(store_apps, "fetch_googleplay_details", side_effect=subprocess.TimeoutExpired("node", 45)):
            store_apps.refresh_googleplay(apps, list(apps), "2026-11-18", lambda _: None)
        self.assertEqual(apps, before)

    def test_apple_rating_count_is_not_review_count_and_zero_is_preserved(self):
        apps = {"ios:us:123": {"id": "123", "country": "us"}}
        with patch.object(store_apps, "lookup_apps", return_value=[{
            "trackId": 123, "averageUserRating": 0, "userRatingCount": 0,
        }]), patch.object(store_apps.time, "sleep"):
            store_apps.refresh_ios(apps, list(apps), "2026-11-19", lambda _: None)
        record = apps["ios:us:123"]
        self.assertEqual(record["rating"], 0)
        self.assertEqual(record["rating_count"], 0)
        self.assertNotIn("review_count", record)
        self.assertNotIn("installs", record)

    def test_apple_video_refresh_is_bounded_and_failures_keep_videos(self):
        apps = {f'ios:us:{i}': {'id': str(i), 'country': 'us', 'videos': [{'url': 'keep'}]} for i in range(15)}
        with patch.object(store_apps, 'apple_video_record', return_value={'video_error': 'timeout', 'video_attempted_at': '2026-11-19'}) as fetch:
            store_apps.refresh_ios_videos(apps, list(apps), '2026-11-19')
            store_apps.refresh_ios_videos(apps, list(apps), '2026-11-19')
        self.assertEqual(fetch.call_count, 15)
        self.assertTrue(all(app['videos'] == [{'url': 'keep'}] for app in apps.values()))


if __name__ == "__main__":
    unittest.main()

"""US release monitoring checks with isolated data and no network."""
import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import release_monitor as monitor

NOW = datetime(2026, 11, 18, 4, tzinfo=timezone.utc)


def app(released="Nov 17, 2026", **extra):
    return {"appId": "com.example.game", "title": "Example", "genreId": "GAME_PUZZLE",
            "released": released, "developer": "Studio", "free": True, "price": 0,
            "description": "Official introduction", "version": "1.2", "icon": "https://play-lh.googleusercontent.com/icon",
            "screenshots": ["https://play-lh.googleusercontent.com/shot"], **extra}


class ReleaseTests(unittest.TestCase):
    def test_official_classification_price_and_future_date(self):
        record = {"platform": "android", "id": "com.example.game"}
        verified = monitor.assess(record, app(), NOW)
        self.assertEqual(verified["status"], "verified")
        self.assertTrue(verified["free"])
        paid = monitor.assess(record, app(free=False, price=2.99), NOW)
        self.assertFalse(paid["free"])
        self.assertEqual(paid["price"], 2.99)
        self.assertEqual(monitor.assess(record, app("Jan 14, 2027"), NOW)["status"], "scheduled")
        self.assertEqual(monitor.assess(record, app(""), NOW)["status"], "undated")
        self.assertEqual(monitor.assess(record, app(genreId="TOOLS"), NOW)["status"], "not_game")
        with self.assertRaisesRegex(ValueError, "分类"):
            monitor.assess(record, app(genreId=None), NOW)
        self.assertEqual(monitor.assess(record, app("Jan 1, 2020", updated=1794950000), NOW)["release_date"], "2020-01-01")

    def test_apple_game_and_identity_validation(self):
        record = {"platform": "ios", "id": "123"}
        detail = {"trackId": 123, "trackName": "Example", "genreIds": ["6014"],
                  "releaseDate": "2026-11-17T12:00:00Z", "price": 1.99,
                  "description": "Official iOS introduction", "version": "2.0", "artistName": "Studio",
                  "artworkUrl512": "https://is1-ssl.mzstatic.com/image/icon.png",
                  "screenshotUrls": ["https://is1-ssl.mzstatic.com/image/phone.png"],
                  "ipadScreenshotUrls": ["https://is1-ssl.mzstatic.com/image/ipad.png"]}
        self.assertEqual(monitor.assess(record, detail, NOW)["status"], "verified")
        assessed = monitor.assess(record, detail, NOW)
        self.assertEqual(assessed["description"], "Official iOS introduction")
        self.assertEqual(len(assessed["screenshots"]), 2)
        self.assertEqual(assessed["media_status"], "complete")
        self.assertEqual(monitor.assess(record, {**detail, "genreIds": ["6002"]}, NOW)["status"], "not_game")
        with self.assertRaises(ValueError):
            monitor.assess(record, {**detail, "trackId": 999}, NOW)
        self.assertEqual(monitor.assess(record, None, NOW)["status"], "unavailable")
        tv = {**detail, "screenshotUrls": [], "ipadScreenshotUrls": [],
              "appletvScreenshotUrls": ["https://is1-ssl.mzstatic.com/image/tv.png"], "supportedDevices": ["AppleTV4-AppleTV4"]}
        tv_result = monitor.assess(record, tv, NOW)
        self.assertEqual(tv_result["screenshots"], tv["appletvScreenshotUrls"])
        self.assertEqual(tv_result["supported_platforms"], ["Apple TV"])
        self.assertEqual(tv_result["media_status"], "complete")

    def test_feed_filters_games_deduplicates_and_keeps_source_date(self):
        entry = {"id": {"attributes": {"im:id": "123"}}, "im:name": {"label": "Game"},
                 "category": {"attributes": {"im:id": "6014"}},
                 "im:releaseDate": {"label": "2026-11-16T00:00:00-07:00"}}
        feed = {"feed": {"updated": {"label": "2026-11-16T10:00:00Z"}, "entry": [
            entry, entry, {**entry, "category": {"attributes": {"im:id": "6002"}}}]}}
        rows, updated = monitor.apple_candidates(feed, "apple:newapplications")
        self.assertEqual(len(rows), 1)
        self.assertEqual(updated, "2026-11-16T10:00:00Z")

    def test_first_discovery_survives_repeats_and_failures(self):
        catalog = {"apps": {}, "sources": {}}
        candidate = {"platform": "android", "id": "com.example.game", "name": "Example", "source": "google:new_free"}
        monitor.add_candidates(catalog, [candidate, candidate], NOW.isoformat())
        self.assertEqual(len(catalog["apps"]), 1)
        before = copy.deepcopy(catalog["apps"])
        monitor.add_candidates(catalog, [candidate], "2026-11-19T04:00:00Z")
        self.assertEqual(catalog["apps"], before)
        catalog["sources"]["google:new_free"] = {"last_success_at": "2026-11-17T04:00:00Z"}
        def fail():
            raise TimeoutError("network timeout")
        monitor.collect_source(catalog, "google:new_free", fail, NOW.isoformat())
        self.assertEqual(catalog["apps"], before)
        self.assertIn("timeout", catalog["sources"]["google:new_free"]["error"])
        self.assertEqual(catalog["sources"]["google:new_free"]["last_success_at"], "2026-11-17T04:00:00Z")

    def test_limit_resume_and_never_relabel_old_release(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            candidates = [{"platform": "android", "id": f"com.example.g{i}", "name": str(i), "source": "test"} for i in range(45)]
            def discover(catalog, data_dir, now):
                monitor.add_candidates(catalog, candidates, now.isoformat())
            def detail(record):
                return app("Jan 1, 2020", appId=record["id"])
            with patch.object(monitor, "discover", side_effect=discover), patch.object(monitor, "fetch_detail", side_effect=detail) as fetch:
                first = monitor.run(root, now=NOW, log=lambda _: None)
                self.assertEqual(fetch.call_count, 40)
                self.assertEqual(sum(r["status"] == "pending" for r in first["apps"]), 5)
                monitor.run(root, now=NOW, log=lambda _: None)
                self.assertEqual(fetch.call_count, 45)
            self.assertTrue(all(r.get("release_date") == "2020-01-01" for r in first["apps"] if r["status"] == "verified"))
            self.assertTrue((root / "releases/catalog.json").exists())

    def test_cached_official_detail_is_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "base").mkdir()
            (root / "base/store-apps.json").write_text(json.dumps({"android:com.example.game": {"release_info": app()}}), encoding="utf-8")
            def discover(catalog, data_dir, now):
                monitor.add_candidates(catalog, [{"platform": "android", "id": "com.example.game", "name": "Example", "source": "test"}], now.isoformat())
            with patch.object(monitor, "discover", side_effect=discover), patch.object(monitor, "fetch_detail") as fetch:
                result = monitor.run(root, now=NOW, log=lambda _: None)
            fetch.assert_not_called()
            self.assertEqual(result["apps"][0]["status"], "verified")

    def test_failed_detail_remains_pending_then_supplement_repairs_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def discover(catalog, data_dir, now):
                monitor.add_candidates(catalog, [{"platform": "android", "id": "com.example.game", "name": "Example", "source": "test"}], now.isoformat())
            with patch.object(monitor, "discover", side_effect=discover), patch.object(monitor, "fetch_detail", side_effect=TimeoutError("timeout")):
                first = monitor.run(root, now=NOW, log=lambda _: None)
            self.assertEqual(first["apps"][0]["status"], "pending")
            with patch.object(monitor, "discover", side_effect=discover), patch.object(monitor, "fetch_detail", return_value=app()):
                second = monitor.run(root, now=NOW, log=lambda _: None)
            self.assertEqual(second["apps"][0]["status"], "verified")
            self.assertEqual(second["apps"][0]["first_discovered_at"], first["apps"][0]["first_discovered_at"])

    def test_old_verified_record_gets_basic_media_repair_and_shared_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            record = {"platform": "android", "id": "com.example.game", "name": "Example", "source": "test",
                      "sources": ["test"], "status": "verified", "release_date": "2026-11-17", "first_discovered_at": "2026-11-17T04:00:00Z"}
            monitor.save(root / "releases/catalog.json", {"apps": {"android:com.example.game": record}, "sources": {}})
            with patch.object(monitor, "discover"), patch.object(monitor, "fetch_detail", return_value=app(score=4.8, ratings=50, reviews=0, installs="1,000+", previewVideo="https://play-games.googleusercontent.com/vp/mp4/preview.mp4")) as fetch:
                result = monitor.run(root, now=NOW, log=lambda _: None)
                monitor.run(root, now=NOW, log=lambda _: None)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(result["apps"][0]["media_status"], "complete")
            store = json.loads((root / "base/store-apps.json").read_text(encoding="utf-8"))
            self.assertEqual(store["android:com.example.game"]["description"], "Official introduction")
            self.assertTrue(store["android:com.example.game"]["screenshots"])
            self.assertEqual(result["apps"][0]["rating"], 4.8)
            self.assertEqual(store["android:com.example.game"]["review_count"], 0)
            self.assertEqual(store["android:com.example.game"]["release_info"]["ratings"], 50)
            self.assertEqual(store["android:com.example.game"]["videos"], result["apps"][0]["videos"])
            self.assertEqual(result["apps"][0]["videos"][0]["type"], 'mp4')

    def test_missing_official_screenshots_is_explicit_and_not_retried_same_day(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def discover(catalog, data_dir, now):
                monitor.add_candidates(catalog, [{"platform": "android", "id": "com.example.game", "name": "Example", "source": "test"}], now.isoformat())
            with patch.object(monitor, "discover", side_effect=discover), patch.object(monitor, "fetch_detail", return_value=app(screenshots=[])) as fetch:
                result = monitor.run(root, now=NOW, log=lambda _: None)
                monitor.run(root, now=NOW, log=lambda _: None)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(result["apps"][0]["media_status"], "missing")
            self.assertIn("screenshots", result["apps"][0]["media_missing"])
            with patch.object(monitor, "discover", side_effect=discover), patch.object(monitor, "fetch_detail", return_value=app()) as fetch:
                repaired = monitor.run(root, now=NOW + timedelta(days=1), log=lambda _: None)
            fetch.assert_called_once()
            self.assertEqual(repaired["apps"][0]["media_status"], "complete")
            store = json.loads((root / "base/store-apps.json").read_text(encoding="utf-8"))
            self.assertEqual(store["android:com.example.game"]["media_missing"], [])

    def test_sparse_response_never_erases_previously_saved_media(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            record = {"platform": "android", "id": "com.example.game", "name": "Example", "source": "test",
                      "sources": ["test"], "status": "verified", "first_discovered_at": "2026-11-17T04:00:00Z",
                      "description": "Keep description", "version": "1.0", "icon": "https://play-lh.googleusercontent.com/icon",
                      "screenshots": ["https://play-lh.googleusercontent.com/keep"], "publisher": "Studio"}
            monitor.save(root / "releases/catalog.json", {"apps": {"android:com.example.game": record}, "sources": {}})
            with patch.object(monitor, "discover"), patch.object(monitor, "fetch_detail", return_value=app(description="", version="", screenshots=[], icon="")):
                result = monitor.run(root, now=NOW, log=lambda _: None)
            self.assertEqual(result["apps"][0]["description"], "Keep description")
            self.assertEqual(result["apps"][0]["screenshots"], record["screenshots"])
            self.assertEqual(result["apps"][0]["media_status"], "complete")

    def test_free_chart_only_app_gets_media_but_is_not_a_new_release_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def discover(catalog, data_dir, now):
                monitor.add_candidates(catalog, [{"platform": "android", "id": "com.example.game", "name": "Example", "source": "google:free"}], now.isoformat())
            with patch.object(monitor, "discover", side_effect=discover), patch.object(monitor, "fetch_detail", return_value=app()):
                result = monitor.run(root, now=NOW, log=lambda _: None)
            self.assertEqual(result["apps"], [])
            self.assertEqual(result["basic_media"], {"total": 1, "complete": 1})
            self.assertTrue((root / "base/store-apps.json").exists())


if __name__ == "__main__":
    unittest.main()

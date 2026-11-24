"""Official store dates remain separate from collection timestamps."""
import unittest

from store_apps import store_date, store_dates


class StoreDateTests(unittest.TestCase):
    def test_apple_dates_keep_official_calendar_day(self):
        self.assertEqual(store_dates({
            "releaseDate": "2012-11-14T23:41:32Z",
            "currentVersionReleaseDate": "2026-11-18T06:26:06Z",
        }, "ios"), {"release_date": "2012-11-14", "last_updated_date": "2026-11-18"})

    def test_google_dates_accept_release_text_and_update_milliseconds(self):
        self.assertEqual(store_dates({"released": "Nov 14, 2012", "updated": 1576868577000}, "android"),
                         {"release_date": "2012-11-14", "last_updated_date": "2019-12-20"})
        self.assertEqual(store_date("November 14, 2012"), "2012-11-14")

    def test_missing_or_invalid_dates_do_not_replace_saved_fields(self):
        saved = {"last_updated_date": "2026-11-18"}
        saved.update(store_dates({"currentVersionReleaseDate": "invalid"}, "ios"))
        self.assertEqual(saved, {"last_updated_date": "2026-11-18"})
        for value in (None, "", "not a date", True, 0, float("inf")):
            self.assertIsNone(store_date(value))


if __name__ == "__main__":
    unittest.main()

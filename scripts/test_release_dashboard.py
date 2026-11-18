"""Release views keep old discoveries out of today's release filter."""
import json
import base64
import unittest
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright


class ReleaseDashboardTests(unittest.TestCase):
    def test_release_filters_and_existing_chart_navigation(self):
        site = Path(__file__).resolve().parents[1] / "site"
        record = {"platform": "ios", "id": "123", "name": "Old paid game", "status": "verified",
                  "release_date": "2026-11-16", "first_discovered_at": "2026-11-18T04:00:00Z",
                  "icon": "https://example.test/icon.png",
                  "free": False, "price": 2.99, "sources": ["apple:newpaidapplications"]}
        fixtures = {
            "data/index.json": {"daily": ["2026-11-17", "2026-11-18"], "diff": []},
            "data/base/game-icons.json": {}, "data/base/store-apps.json": {},
            "data/daily/2026-11-18.json": {"platforms": {"android": {"boards": [{"label": "美区免费榜", "rows": [{"name": "Existing", "rank": 1}]}]}}},
            "data/releases/2026-11-18.json": {"date_beijing": "2026-11-18", "coverage": "Partial official coverage", "sources": {
                "apple:newpaidapplications": {"error": "Retry timeout", "last_success_at": "2026-11-17T00:00:00Z"}},
                "apps": [record, {**record, "id": "124", "name": "Pending game", "status": "pending", "release_date": None},
                          {**record, "id": "125", "name": "Future game", "status": "scheduled", "release_date": "2027-01-14"},
                          {**record, "platform": "android", "id": "com.example.game", "name": "New free game",
                                   "free": True, "price": 0, "release_date": "2026-11-18"}]},
        }
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            def serve(route):
                path = urlparse(route.request.url).path.lstrip("/") or "index.html"
                if path == "icon.png":
                    route.fulfill(content_type="image/png", body=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aV1EAAAAASUVORK5CYII='))
                elif path in fixtures:
                    route.fulfill(content_type="application/json", body=json.dumps(fixtures[path]))
                elif path.startswith("data/"):
                    route.fulfill(status=404, body="missing")
                elif (site / path).is_file():
                    route.fulfill(content_type={".js": "text/javascript", ".css": "text/css"}.get(Path(path).suffix, "text/html"), body=(site / path).read_bytes())
                else:
                    route.abort()
            page.route("**/*", serve)
            try:
                page.goto("http://dashboard.test/", wait_until="domcontentloaded")
                page.locator('.platform-tab[data-plat="ios"]').click()
                page.locator('.board-tab[data-label="美区每日新上架"]').click()
                page.wait_for_function("document.getElementById('release-count').textContent.includes('显示 0')")
                self.assertEqual(page.locator('#release-status').input_value(), 'all')
                self.assertEqual(page.locator('#release-day').input_value(), '2026-11-18')
                self.assertIn('监测日发布 0', page.locator('#release-count').inner_text())
                page.locator('#release-day').select_option('')
                self.assertEqual(page.locator('#tbl-releases tbody tr').count(), 3)
                self.assertIn('Old paid game', page.locator('#tbl-releases tbody').inner_text())
                first = page.locator('#tbl-releases tbody tr').filter(has_text='Old paid game')
                self.assertEqual(first.locator('.rank-game-icon img').get_attribute('src'), 'https://example.test/icon.png')
                query = parse_qs(urlparse(first.locator('.profile-link').get_attribute('href')).query)
                self.assertEqual(query['store'], ['ios:us:123'])
                self.assertEqual(query['release'], ['2026-11-18'])
                self.assertIn('Pending game', page.locator('#tbl-releases tbody').inner_text())
                self.assertIn('Future game', page.locator('#tbl-releases tbody').inner_text())
                page.locator('#release-status').select_option('verified')
                self.assertEqual(page.locator('#tbl-releases tbody tr').count(), 1)
                page.locator('#release-day').select_option('2026-11-18')
                self.assertNotIn("Old paid game", page.locator("#tbl-releases tbody").inner_text())
                self.assertIn("Retry timeout", page.locator('#release-sources').inner_text())
                page.locator('.platform-tab[data-plat="android"]').click()
                self.assertIn('Google Play', page.locator('.platform-tab[data-plat="android"]').inner_text())
                page.locator('.board-tab[data-label="美区每日新上架"]').click()
                page.wait_for_function("document.querySelector('#tbl-releases tbody').textContent.includes('New free game')")
                query = parse_qs(urlparse(page.locator('#tbl-releases .profile-link').get_attribute('href')).query)
                self.assertEqual(query['store'], ['android:com.example.game'])
                page.locator('.board-tab[data-label="美区免费榜"]').click()
                page.wait_for_function("document.querySelector('#tbl-full tbody').textContent.includes('Existing')")
                self.assertTrue(page.locator('#panel-releases').is_hidden())
                page.locator('.board-tab[data-label="美区每日新上架"]').click()
                page.locator('#date-picker').select_option('2026-11-17')
                page.locator('#date-load').click()
                page.wait_for_function("document.querySelector('#tbl-releases tbody').textContent.includes('尚无上架监控快照')")
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()

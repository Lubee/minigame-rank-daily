"""A slow optional icon cache must not hide a successfully loaded chart."""
import json
import unittest
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright


class DashboardTests(unittest.TestCase):
    def test_chart_does_not_wait_for_store_icons(self):
        site = Path(__file__).resolve().parents[1] / "site"
        day = "2026-11-18"
        snapshot = {"platforms": {"android": {"boards": [
            {"label": "美区免费榜", "rows": [{"rank": 1, "name": "Example", "package": "com.example.game"}]}]}}}
        fixtures = {"data/index.json": {"daily": [day], "diff": []},
                    f"data/daily/{day}.json": snapshot, "data/base/game-icons.json": {}}
        pending = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            def serve(route):
                path = urlparse(route.request.url).path.lstrip("/") or "index.html"
                if path == "data/base/store-apps.json":
                    pending.append(route)
                elif path in fixtures:
                    route.fulfill(content_type="application/json", body=json.dumps(fixtures[path]))
                elif (site / path).is_file():
                    content_type = {".js": "text/javascript", ".css": "text/css"}.get(Path(path).suffix, "text/html")
                    route.fulfill(content_type=content_type, body=(site / path).read_bytes())
                else:
                    route.abort()
            page.route("**/*", serve)
            page.goto("http://dashboard.test/", wait_until="domcontentloaded")
            page.locator('.platform-tab[data-plat="android"]').click()
            try:
                page.wait_for_function("document.getElementById('panel-board-name').textContent.includes('共 1 条')", timeout=1500)
                self.assertIn("Example", page.locator("#tbl-full tbody").inner_text())
                self.assertTrue(pending)
            finally:
                for route in pending:
                    route.fulfill(content_type="application/json", body="{}")
                browser.close()


if __name__ == "__main__":
    unittest.main()

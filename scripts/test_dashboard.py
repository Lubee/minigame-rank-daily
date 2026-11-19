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
                for count in (0, 5, 6):
                    page.evaluate('''count => {
                        const rows = Array.from({length: count}, (_, i) => ({name: `Game ${i}`, rank: i + 1}));
                        state.newProductsExpanded = false;
                        state.cache[state.currentDate]['android/美区免费榜'] = {rows, diff: {new_to_board: rows}};
                        renderDiffTables();
                    }''', count)
                    self.assertEqual(page.locator('#tbl-new tbody tr .rank-num').count(), min(count, 5))
                    if count > 5:
                        page.get_by_role('button', name='加载剩余 1 条').click()
                        self.assertEqual(page.locator('#tbl-new tbody tr .rank-num').count(), 6)
                        page.get_by_role('button', name='收起新进产品').click()
                        self.assertEqual(page.locator('#tbl-new tbody tr .rank-num').count(), 5)
                        page.get_by_role('button', name='加载剩余 1 条').click()
                        page.evaluate("setActiveBoard('android', '美区免费榜')")
                        self.assertEqual(page.locator('#tbl-new tbody tr .rank-num').count(), 5)
                    else:
                        self.assertEqual(page.locator('#new-products-toggle:visible').count(), 0)
            finally:
                for route in pending:
                    route.fulfill(content_type="application/json", body="{}")
                browser.close()


if __name__ == "__main__":
    unittest.main()

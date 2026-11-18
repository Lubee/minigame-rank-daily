"""Profiles merge complete release metadata over sparse older store records."""
import base64
import json
import unittest
from pathlib import Path
from urllib.parse import urlencode, urlparse
from playwright.sync_api import sync_playwright


class ReleaseProfileTests(unittest.TestCase):
    def test_official_description_and_screenshots_are_preserved_for_both_stores(self):
        site = Path(__file__).resolve().parents[1] / "site"
        pixel = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aV1EAAAAASUVORK5CYII=')
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                for platform, has_shots in (("ios", True), ("android", True), ("android", False)):
                    with self.subTest(platform=platform, has_shots=has_shots):
                        app_id = "123" if platform == "ios" else "com.example.game"
                        key = f"ios:us:{app_id}" if platform == "ios" else f"android:{app_id}"
                        shot = "https://is1-ssl.mzstatic.com/test.png" if platform == "ios" else "https://play-lh.googleusercontent.com/test.png"
                        app = {"platform": platform, "id": app_id, "name": "Example", "publisher": "Studio", "country": "us",
                               "description": "Official game introduction", "version": "1.2", "screenshots": [shot] if has_shots else [],
                               "media_checked_at": "2026-11-18T04:00:00Z"}
                        fixtures = {"data/base/store-apps.json": {key: {"platform": platform, "country": "us", "id": app_id,
                            "name": "Example", "description": "", "version": "", "screenshots": []}},
                            "data/base/games.json": {"games": {}}, "data/detail/index.json": {"games": {}},
                            "data/releases/2026-11-18.json": {"apps": [app]}}
                        page = browser.new_page()
                        def serve(route):
                            path = urlparse(route.request.url).path.lstrip("/")
                            if route.request.resource_type == "image":
                                route.fulfill(content_type="image/png", body=pixel)
                            elif path in fixtures:
                                route.fulfill(content_type="application/json", body=json.dumps(fixtures[path]))
                            elif route.request.url.startswith('http://profile.test/') and (site / path).is_file():
                                route.fulfill(content_type={".js": "text/javascript", ".css": "text/css"}.get(Path(path).suffix, "text/html"), body=(site / path).read_bytes())
                            else:
                                route.abort()
                        page.route('**/*', serve)
                        page.goto('http://profile.test/game.html?' + urlencode({"name": "Example", "platform": platform,
                            "board": "美区每日新上架", "store": key, "release": "2026-11-18"}), wait_until='domcontentloaded')
                        section = page.locator('#gp-store-apps')
                        section.wait_for(state='visible')
                        self.assertIn('Official game introduction', section.inner_text())
                        self.assertIn('1.2', section.inner_text())
                        self.assertEqual(section.locator('.gp-store-shots img').count(), int(has_shots))
                        if has_shots:
                            section.locator('.gp-store-shots img').dispatch_event('error')
                            self.assertIn('官方截图暂时加载失败', section.inner_text())
                        if not has_shots:
                            self.assertIn('本次官方查询未返回完整资料', section.inner_text())
                        page.close()
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()

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
                               "rating": 4.6, "rating_count": 1234, "icon": shot,
                               **({"review_count": 0, "installs": "100,000+"} if platform == "android" else {}),
                               "metrics_checked_at": "2026-11-18T04:00:00Z",
                               "description": "Official game introduction" + ("\nSecond line\nThird line\nFourth line\nFifth line" if has_shots else ""), "version": "1.2", "screenshots": [shot] if has_shots else [],
                               "videos": [{"url": "https://play-games.googleusercontent.com/vp/mp4/test.mp4", "type": "mp4", "source": "Google Play", "poster": shot}] if platform == "android" and has_shots else [],
                               "media_checked_at": "2026-11-18T04:00:00Z"}
                        fixtures = {"data/base/store-apps.json": {key: {"platform": platform, "country": "us", "id": app_id,
                            "name": "Example", "description": "", "version": "", "screenshots": []}},
                            "data/base/games.json": {"games": {}}, "data/detail/index.json": {"games": {}},
                            "data/releases/2026-11-18.json": {"apps": [app]}}
                        if has_shots:
                            app.update({"release_info": {"releaseDate": "2012-11-14T23:41:32Z", "currentVersionReleaseDate": "2026-11-18T06:26:06Z"}} if platform == "ios" else
                                       {"release_info": {"released": "Nov 14, 2012", "updated": 1576868577000}})
                        page = browser.new_page()
                        page.set_default_timeout(2000)
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
                        release_date = section.locator('dt', has_text='发布日期').locator('+ dd').inner_text()
                        updated_date = section.locator('dt', has_text='最近更新日期').locator('+ dd').inner_text()
                        self.assertEqual(release_date, '2012-11-14' if has_shots else '官方暂未提供')
                        self.assertEqual(updated_date, ('2026-11-18' if platform == 'ios' else '2019-12-20') if has_shots else '官方暂未提供')
                        description = section.locator('.gp-app-description')
                        self.assertEqual(description.evaluate('(el)=>getComputedStyle(el).webkitLineClamp'), '3')
                        if has_shots:
                            section.get_by_role('button', name='展开简介').click()
                            self.assertEqual(description.evaluate('(el)=>getComputedStyle(el).webkitLineClamp'), 'none')
                            section.get_by_role('button', name='收起简介').click()
                        else:
                            self.assertEqual(section.get_by_role('button', name='展开简介').count(), 0)
                        self.assertIn('4.6 / 5', section.inner_text())
                        self.assertIn('评分人数', section.inner_text())
                        self.assertIn('1,234', section.inner_text())
                        self.assertEqual(section.locator('dt', has_text='评论数').count(), int(platform == 'android'))
                        if platform == 'android':
                            self.assertEqual(section.locator('dt', has_text='评论数').locator('+ dd').inner_text(), '0')
                            self.assertIn('100,000+', section.inner_text())
                        self.assertNotIn('最近一个月下载量', section.inner_text())
                        icon = page.locator('.gp-profile-icon img')
                        self.assertEqual(icon.get_attribute('src'), shot)
                        page.set_viewport_size({"width": 390, "height": 844})
                        self.assertLessEqual(page.locator('.gp-profile-icon').bounding_box()['x'] + page.locator('.gp-profile-icon').bounding_box()['width'], 390)
                        icon.dispatch_event('error')
                        self.assertEqual(page.locator('.gp-profile-icon img').count(), 0)
                        self.assertEqual(section.locator('.gp-store-shots a img').count(), int(has_shots))
                        if has_shots:
                            if platform == 'android':
                                self.assertEqual(section.locator('.gp-store-shots').locator(':scope > :first-child').get_attribute('class'), 'gp-video-preview')
                                self.assertEqual(page.locator('#gp-video-player video').count(), 0)
                                section.get_by_role('button', name='播放官方视频预览').click()
                                self.assertEqual(page.locator('#gp-video-player video').get_attribute('preload'), 'none')
                                page.get_by_role('button', name='关闭视频').click()
                                page.locator('#gp-video-player').wait_for(state='detached')
                                self.assertEqual(page.locator('#gp-video-player').count(), 0)
                            section.locator('.gp-store-shots a img').dispatch_event('error')
                            if platform == 'ios':
                                self.assertIn('官方截图暂时加载失败', section.inner_text())
                        if not has_shots:
                            self.assertIn('本次官方查询未返回完整资料', section.inner_text())
                        page.close()
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()

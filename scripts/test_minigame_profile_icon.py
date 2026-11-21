"""WX/Douyin profiles reuse platform-specific icons without store lookups."""
import base64
import json
import unittest
from pathlib import Path
from urllib.parse import urlencode, urlparse
from playwright.sync_api import sync_playwright


class MiniGameIconTests(unittest.TestCase):
    def test_snapshot_cache_history_and_detail_icons(self):
        site = Path(__file__).resolve().parents[1] / 'site'
        pixel = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aV1EAAAAASUVORK5CYII=')
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                for platform, source in [('wx', 'snapshot'), ('douyin', 'latest'), ('wx', 'cache'), ('wx', 'history'), ('douyin', 'detail')]:
                    with self.subTest(platform=platform, source=source):
                        icon = f'https://gravity-client-resource.oss-cn-hangzhou.aliyuncs.com/{platform}.png'
                        row = {'name': 'Example', 'app_id': 'example-id'}
                        if source not in ('cache', 'detail'):
                            row['icon'] = icon
                        snapshot = {'platforms': {platform: {'boards': [{'label': '畅销榜', 'rows': [row]}]},
                            ('douyin' if platform == 'wx' else 'wx'): {'boards': [{'label': '畅销榜', 'rows': [{'name': 'Example', 'icon': 'https://example.com/wrong.png'}]}]}}}
                        fixtures = {
                            'data/latest.json': snapshot if source in ('latest', 'cache') else {},
                            'data/daily/2026-11-19.json': snapshot,
                            'data/base/game-icons.json': {platform: {'example-id': icon}},
                            'data/base/games.json': {'games': {'Example': {'board_history': {f'{platform}/畅销榜': {'last_seen': '2026-11-19'}}}}} if source == 'history' else {'games': {}},
                            'data/detail/index.json': {'games': {'Example': {'icon': icon, 'boards': [f'{platform}/畅销榜']}}} if source == 'detail' else {'games': {}},
                        }
                        page = browser.new_page()
                        def serve(route):
                            path = urlparse(route.request.url).path.lstrip('/')
                            if route.request.resource_type == 'image':
                                route.fulfill(content_type='image/png', body=pixel)
                            elif path in fixtures:
                                route.fulfill(content_type='application/json', body=json.dumps(fixtures[path]))
                            elif route.request.url.startswith('http://profile.test/') and (site / path).is_file():
                                route.fulfill(content_type={'.js': 'text/javascript', '.css': 'text/css'}.get(Path(path).suffix, 'text/html'), body=(site / path).read_bytes())
                            else:
                                route.abort()
                        page.route('**/*', serve)
                        query = {'name': 'Example', 'platform': platform, 'board': '畅销榜'}
                        if source == 'snapshot':
                            query['snapshot'] = '2026-11-19'
                        page.goto('http://profile.test/game.html?' + urlencode(query), wait_until='domcontentloaded')
                        page.wait_for_function('(url) => document.querySelector(".gp-profile-icon img")?.src === url', arg=icon, timeout=2000)
                        self.assertEqual(page.locator('#gp-store-apps').count(), 0)
                        page.locator('.gp-profile-icon img').dispatch_event('error')
                        self.assertEqual(page.locator('.gp-profile-icon').inner_text(), 'E')
                        page.close()
            finally:
                browser.close()

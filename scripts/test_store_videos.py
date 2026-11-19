"""Official media extraction must reject redirects and unrelated applications."""
import json
import unittest
from unittest.mock import patch

import store_videos


class VideoTests(unittest.TestCase):
    def test_apple_failure_preserves_existing_video(self):
        existing = {'videos': [{'url': 'https://apptrailers.itunes.apple.com/keep.m3u8'}]}
        with patch.object(store_videos, 'fetch_apple_videos', side_effect=ValueError('Wrong app')):
            existing.update(store_videos.apple_video_record('123', 'us', '2026-11-19'))
        self.assertEqual(len(existing['videos']), 1)
        self.assertIn('Wrong app', existing['video_error'])

    def test_google_direct_preview_and_official_embed_fallback(self):
        detail = {"previewVideo": "https://play-games.googleusercontent.com/vp/mp4/preview.mp4",
                  "video": "https://www.youtube.com/embed/abcdefghijk?rel=0",
                  "videoImage": "https://play-lh.googleusercontent.com/poster"}
        videos = store_videos.google_videos(detail)
        self.assertEqual(videos[0]["type"], "mp4")
        self.assertEqual(videos[0]["fallback_url"], detail["video"])
        self.assertEqual(store_videos.google_videos({"video": detail["video"]})[0]["type"], "youtube")
        self.assertEqual(store_videos.google_videos({"previewVideo": "https://evil.example/a.mp4"}), [])

    def test_apple_identity_and_preview_selection(self):
        video = {"$kind": "Video", "videoUrl": "https://apptrailers.itunes.apple.com/a.m3u8",
                 "preview": {"template": "https://is1-ssl.mzstatic.com/image/{w}x{h}{c}.{f}"},
                 "templateMediaEvent": {"fields": {"pageId": "123"}}}
        payload = {"data": [{"data": {"media": [video, {**video, "videoUrl": "https://apptrailers.itunes.apple.com/other.m3u8",
                    "templateMediaEvent": {"fields": {"pageId": "999"}}}]}}]}
        html = '<link rel="canonical" href="https://apps.apple.com/us/app/example/id123">' + (
            '<script id="serialized-server-data" type="application/json">' + json.dumps(payload) + '</script>')
        videos = store_videos.apple_videos(html, 'https://apps.apple.com/us/app/id123', '123', 'us')
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]["type"], "hls")
        self.assertNotIn('{', videos[0]["poster"])
        for final in ('https://apps.apple.com/cn/iphone/today', 'https://apps.apple.com/us/app/id999'):
            with self.assertRaises(ValueError):
                store_videos.apple_videos(html, final, '123', 'us')

    def test_apple_jsonld_app_preview_and_empty_media(self):
        app = {"@type": "SoftwareApplication", "url": "https://apps.apple.com/us/app/id123",
               "video": {"@type": "VideoObject", "contentUrl": "https://video.itunes.apple.com/a.mp4"}}
        html = '<script type="application/ld+json">' + json.dumps(app) + '</script>'
        self.assertEqual(store_videos.apple_videos(html, app['url'], '123', 'us')[0]['type'], 'mp4')
        self.assertEqual(store_videos.apple_videos(html.replace('"video":', '"ignored":'), app['url'], '123', 'us'), [])

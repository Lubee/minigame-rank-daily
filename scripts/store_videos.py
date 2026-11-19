"""Keep official preview URLs only; never download movie data."""
from __future__ import annotations

import json
import re
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urlparse

VIDEO_FIELDS = ("video", "previewVideo", "videoImage")


def video_url(url, platform):
    if not isinstance(url, str):
        return False
    parsed = urlparse(url)
    hosts = ("apptrailers.itunes.apple.com", "video.itunes.apple.com") if platform == "ios" else (
        "play-games.googleusercontent.com", "www.youtube.com", "www.youtube-nocookie.com")
    return parsed.scheme == "https" and parsed.hostname in hosts and not parsed.username and not parsed.password and parsed.port in (None, 443)


def google_videos(detail):
    direct, embed = detail.get("previewVideo"), detail.get("video")
    if not (video_url(embed, "android") and re.fullmatch(r"/embed/[A-Za-z0-9_-]{11}", urlparse(embed).path)):
        embed = None
    poster = detail.get("videoImage") or ""
    if not poster.startswith("https://play-lh.googleusercontent.com/"):
        poster = ""
    if video_url(direct, "android") and urlparse(direct).hostname == "play-games.googleusercontent.com" and urlparse(direct).path.endswith(".mp4"):
        return [{"url": direct, "type": "mp4", "poster": poster, "source": "Google Play", "fallback_url": embed or ""}]
    return [{"url": embed, "type": "youtube", "poster": poster, "source": "Google Play"}] if embed else []


class _Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts, self.canonical, self.current = [], "", None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link" and attrs.get("rel") == "canonical":
            self.canonical = attrs.get("href", "")
        if tag == "script" and (attrs.get("id") == "serialized-server-data" or attrs.get("type") == "application/ld+json"):
            self.current = []

    def handle_data(self, data):
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.current is not None:
            self.scripts.append("".join(self.current))
            self.current = None


def _app_url(url, app_id, country):
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname == "apps.apple.com" and bool(
        re.fullmatch(rf"/{re.escape(country)}/app/(?:[^/]+/)?id{re.escape(app_id)}/*", parsed.path))


def apple_videos(html, final_url, app_id, country="us"):
    if not _app_url(final_url, app_id, country):
        raise ValueError("Apple 视频页面跳转到首页、其他地区或其他应用")
    page = _Page()
    page.feed(html)
    if page.canonical and not _app_url(page.canonical, app_id, country):
        raise ValueError("Apple 视频页面 canonical 与目标应用不符")
    videos, identified = [], False

    def add(url, poster=""):
        if not video_url(url, "ios"):
            return
        kind = "hls" if urlparse(url).path.endswith(".m3u8") else "mp4" if urlparse(url).path.endswith(".mp4") else None
        if not kind or any(item["url"] == url for item in videos):
            return
        if not re.match(r"https://[\w.-]+\.mzstatic\.com/", poster):
            poster = ""
        for token, value in (("{w}", "640"), ("{h}", "360"), ("{c}", "sr"), ("{f}", "jpg")):
            poster = poster.replace(token, value)
        videos.append({"url": url, "type": kind, "poster": poster, "source": "App Store"})

    def walk(value):
        nonlocal identified
        if isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            if value.get("@type") in ("SoftwareApplication", "MobileApplication"):
                if not _app_url(value.get("url", ""), app_id, country):
                    return
                identified = True
                items = value.get("video") or []
                for video in items if isinstance(items, list) else [items]:
                    if isinstance(video, dict):
                        add(video.get("contentUrl"), video.get("thumbnailUrl") if isinstance(video.get("thumbnailUrl"), str) else "")
                return
            fields = value.get("templateMediaEvent", {}).get("fields", {})
            if str(fields.get("pageId", "")) == app_id:
                identified = True
                if value.get("$kind") == "Video":
                    add(value.get("videoUrl"), (value.get("preview") or {}).get("template", ""))
            for item in value.values():
                walk(item)

    for script in page.scripts:
        try:
            walk(json.loads(script))
        except (ValueError, TypeError):
            continue
    if not identified:
        raise ValueError("Apple 官方页面未返回可核对应用身份的视频结构")
    return videos


def fetch_apple_videos(app_id, country="us", timeout=15):
    if not re.fullmatch(r"\d+", str(app_id)) or country not in ("us", "cn", "jp"):
        raise ValueError("无效 Apple 应用身份")
    url = f"https://apps.apple.com/{country}/app/id{app_id}"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        final_url = response.geturl()
        if not _app_url(final_url, str(app_id), country):
            raise ValueError("Apple 视频页面跳转到首页、其他地区或其他应用")
        html = response.read(5_000_001)
        if len(html) > 5_000_000:
            raise ValueError("Apple 视频页面超出大小限制")
    return apple_videos(html.decode("utf-8"), final_url, str(app_id), country)


def apple_video_record(app_id, country, checked_at):
    try:
        return {"videos": fetch_apple_videos(app_id, country), "video_attempted_at": checked_at,
                "video_checked_at": checked_at, "video_error": None}
    except (OSError, ValueError) as error:
        return {"video_attempted_at": checked_at, "video_error": str(error)[:300]}

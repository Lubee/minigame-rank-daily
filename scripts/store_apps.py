"""Accumulate public App Store and Google Play metadata from rank snapshots."""
from __future__ import annotations

import json
import math
import re
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode

from scrape_ios import COUNTRIES, lookup_apps
from scrape_googleplay import fetch_details as fetch_googleplay_details
from store_videos import VIDEO_FIELDS, apple_video_record, google_videos


METRIC_FIELDS = ("averageUserRating", "userRatingCount", "score", "ratings", "reviews", "installs")


def store_metrics(detail: dict, platform: str, checked_at: str):
    fields = (("rating", "averageUserRating"), ("rating_count", "userRatingCount")) if platform == "ios" else (
        ("rating", "score"), ("rating_count", "ratings"), ("review_count", "reviews"))
    result = {}
    for target, source in fields:
        value = detail.get(source)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
            if (target == "rating" and value <= 5) or (target != "rating" and float(value).is_integer()):
                result[target] = value
    installs = detail.get("installs")
    if platform == "android" and isinstance(installs, str) and re.fullmatch(r"[\d,.\s]+\+?", installs):
        result["installs"] = installs
    if result:
        result["metrics_checked_at"] = checked_at
    return result


def chart_apps(snapshot: dict, platform: str):
    boards = snapshot.get("platforms", {}).get(platform, {}).get("boards", [])
    ios_regions = {f"{label}免费榜": country for country, label in COUNTRIES.items()}
    id_field = "package" if platform == "android" else "app_id"
    id_pattern = r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+" if platform == "android" else r"[0-9]+"
    for board in boards:
        country = ios_regions.get(board["label"]) if platform == "ios" else ("cn" if platform == "taptap" else "us")
        if not country:
            continue
        for row in board.get("rows", []):
            app_id = str(row.get(id_field) or "")
            if not row.get("name") or not re.fullmatch(id_pattern, app_id):
                continue
            if platform == "ios":
                key = f"ios:{country}:{app_id}"
                url = f"https://apps.apple.com/{country}/app/id{app_id}"
            elif platform == "android":
                key = f"android:{app_id}"
                url = "https://play.google.com/store/apps/details?" + urlencode({"id": app_id, "gl": country})
            else:
                key = f"taptap:{app_id}"
                url = f"https://www.taptap.cn/app/{app_id}"
            yield key, {
                "platform": platform,
                "country": country,
                "id": app_id,
                "name": row["name"],
                "publisher": row.get("publisher"),
                "category": row.get("category"),
                "url": url,
                "icon": row.get("icon"),
                "screenshots": row.get("screenshots"),
            }


def history_apps(data_dir: Path):
    apps = {}
    for path in sorted((data_dir / "daily").glob("????-??-??.json")):
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for platform in ("ios", "android", "taptap"):
            merge_apps(apps, chart_apps(snapshot, platform))
    return apps


def merge_apps(apps, records):
    for key, record in records:
        app = apps.setdefault(key, {})
        names = set(app.get("names", []))
        names.update(name for name in (app.get("name"), record.get("name")) if name)
        names.update(record.get("names", []))
        app.update({field: value for field, value in record.items() if value})
        app["names"] = sorted(names)


def refresh_ios(apps: dict, keys: list[str], today: str, log):
    cutoff = (date.fromisoformat(today) - timedelta(days=7)).isoformat()
    for country in COUNTRIES:
        pending = [
            key for key in keys
            if apps[key]["country"] == country
            and ("screenshots" not in apps[key] or apps[key].get("checked_on", "") <= cutoff)
        ]
        pending.sort(key=lambda key: apps[key].get("checked_on", ""))
        for start in range(0, len(pending), 25):
            batch = pending[start:start + 25]
            try:
                results = lookup_apps(country, [apps[key]["id"] for key in batch])
            except (OSError, ValueError) as error:
                log(f"[App Store/{country}] 详情查询失败，保留已有资料：{error}")
                if isinstance(error, HTTPError) and error.code == 429:
                    return
                break
            by_id = {str(row.get("trackId")): row for row in results}
            for key in batch:
                app = apps[key]
                result = by_id.get(app["id"], {})
                if result:
                    app["release_info"] = {field: result.get(field) for field in (
                        "trackId", "trackName", "genreIds", "primaryGenreId", "releaseDate", "price",
                        "currency", "sellerName", "artistName", "artworkUrl512", "supportedDevices", *METRIC_FIELDS)}
                    app.update(store_metrics(result, "ios", today))
                for field, source in (
                    ("developer", "sellerName"),
                    ("description", "description"),
                    ("version", "version"),
                ):
                    if result.get(source):
                        app[field] = result[source]
                urls = (result.get("screenshotUrls") or []) + (result.get("ipadScreenshotUrls") or []) + (result.get("appletvScreenshotUrls") or [])
                screenshots = list(dict.fromkeys(
                    url for url in urls if re.match(r"https://[\w.-]+\.mzstatic\.com/", url)
                ))
                if screenshots:
                    app["screenshots"] = screenshots
                else:
                    app.setdefault("screenshots", [])
                app["checked_on"] = today
            time.sleep(0.4)


def refresh_googleplay(apps: dict, keys: list[str], today: str, log):
    cutoff = (date.fromisoformat(today) - timedelta(days=7)).isoformat()
    pending = [key for key in keys if apps[key].get("details_checked_on", "") <= cutoff]
    pending.sort(key=lambda key: apps[key].get("details_checked_on", ""))
    pending = pending[:12]

    def fetch(key):
        try:
            app = fetch_googleplay_details(apps[key]["id"])
            return {
                **store_metrics(app, "android", today),
                "videos": google_videos(app), "video_checked_at": today, "video_error": None,
                "developer": app.get("developer"), "description": app.get("description"),
                "version": app.get("version"), "icon": app.get("icon"),
                "screenshots": app.get("screenshots"),
                "release_info": {field: app.get(field) for field in (
                    "appId", "title", "genreId", "released", "developer", "free", "price", "currency", "icon", *METRIC_FIELDS, *VIDEO_FIELDS)},
            }
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            log(f"[Google Play/{apps[key]['id']}] 详情查询失败，保留已有资料：{error}")
            return None

    with ThreadPoolExecutor(max_workers=3) as pool:
        for key, media in zip(pending, pool.map(fetch, pending)):
            if media is not None:
                apps[key].update({field: value for field, value in media.items() if value or value == 0})
                apps[key]["media_checked_on"] = today
                apps[key]["details_checked_on"] = today
                apps[key]["video_error"] = None


def refresh_ios_videos(apps, keys, today):
    cutoff = (date.fromisoformat(today) - timedelta(days=7)).isoformat()
    pending = [key for key in keys if apps[key].get("country") == "us"
               and apps[key].get("video_checked_at", "")[:10] <= cutoff
               and apps[key].get("video_attempted_at", "")[:10] != today]
    pending.sort(key=lambda key: apps[key].get("video_attempted_at", ""))
    pending = pending[:12]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = pool.map(lambda key: apple_video_record(apps[key]["id"], "us", today), pending)
        for key, result in zip(pending, results):
            apps[key].update(result)


def update_store_apps(snapshot: dict, data_dir: Path, today: str, log=print, *, backfill=False):
    path = data_dir / "base" / "store-apps.json"
    previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    apps = {key: app.copy() for key, app in previous.items()}
    include_history = backfill or not path.exists()
    if include_history:
        archive = history_apps(data_dir)
        merge_apps(archive, apps.items())
        apps = archive
    ios = dict(chart_apps(snapshot, "ios"))
    android = dict(chart_apps(snapshot, "android"))
    merge_apps(apps, ios.items())
    merge_apps(apps, android.items())
    merge_apps(apps, chart_apps(snapshot, "taptap"))
    keys = list(ios)
    if include_history:
        keys = [key for key, app in apps.items() if app["platform"] == "ios"]
    refresh_ios(apps, keys, today, log)
    android_keys = [key for key in reversed(apps) if key.startswith("android:") and "details_checked_on" not in apps[key]]
    refresh_googleplay(apps, list(dict.fromkeys([*android, *android_keys])), today, log)
    refresh_ios_videos(apps, keys, today)
    if apps == previous:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, mode="w", encoding="utf-8", delete=False,
        ) as output:
            temporary = Path(output.name)
            json.dump(apps, output, ensure_ascii=False, indent=2, sort_keys=True)
            output.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    log(f"[商店资料] 保存 {len(apps)} 条")

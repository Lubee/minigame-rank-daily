"""Bounded US game discovery from official stores; discovery is not a release date."""
from __future__ import annotations

import argparse
import json
import re
import tempfile
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import scrape_googleplay as googleplay
from scrape_ios import _fetch_json, _entry_icon
from store_apps import METRIC_FIELDS, store_metrics, store_dates, store_date as release_date
from store_videos import VIDEO_FIELDS, google_videos

ROOT = Path(__file__).resolve().parent.parent
BEIJING = timezone(timedelta(hours=8))


def media_missing(record):
    return [field for field in ("description", "version", "icon", "screenshots", "publisher") if not record.get(field)]


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as output:
            temporary = Path(output.name)
            json.dump(data, output, ensure_ascii=False, indent=2)
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def load(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def assess(record, detail, now):
    if detail is None:
        return {"status": "unavailable"}
    ios = record["platform"] == "ios"
    if str(detail.get("trackId" if ios else "appId")) != record["id"]:
        raise ValueError("官方详情的应用 ID 与候选不一致")
    if (ios and not detail.get("genreIds") and not detail.get("primaryGenreId")) or (not ios and not detail.get("genreId")):
        raise ValueError("官方详情缺少分类，暂不能确认是否为游戏")
    is_game = ("6014" in [str(g) for g in detail.get("genreIds", [])]
               or str(detail.get("primaryGenreId")) == "6014") if ios else (
        detail.get("genreId") == "GAME" or str(detail.get("genreId", "")).startswith("GAME_"))
    if not is_game:
        return {"status": "not_game"}
    name = detail.get("trackName" if ios else "title")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("官方详情缺少名称")
    raw_date = detail.get("releaseDate" if ios else "released")
    released = release_date(raw_date)
    future = bool(released and released > now.astimezone(BEIJING).date().isoformat())
    if ios and released:
        instant = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
        if instant.tzinfo:
            future = instant > now
    price = detail.get("price")
    free = (price == 0 if isinstance(price, (int, float)) else None) if ios else detail.get("free")
    shots = ((detail.get("screenshotUrls") or []) + (detail.get("ipadScreenshotUrls") or [])
             + (detail.get("appletvScreenshotUrls") or [])) if ios else (detail.get("screenshots") or [])
    screenshots = list(dict.fromkeys(url for url in shots if isinstance(url, str) and re.match(
        r"https://(?:[\w.-]+\.mzstatic\.com|play-lh\.googleusercontent\.com)/", url)))
    result = {
        **store_metrics(detail, record["platform"], now.isoformat()),
        **store_dates(detail, record["platform"]),
        **({"videos": google_videos(detail), "video_checked_at": now.isoformat(), "video_error": None} if not ios else {}),
        "status": "scheduled" if future else ("verified" if released else "undated"),
        "name": name.strip(), "release_date": released, "release_date_raw": raw_date,
        "publisher": detail.get("sellerName") or detail.get("artistName") or detail.get("developer") or "",
        "free": free, "price": price, "currency": detail.get("currency"),
        "icon": detail.get("artworkUrl512") or detail.get("icon") or "",
        "description": (detail.get("description") or "").strip(), "version": detail.get("version") or "",
        "screenshots": screenshots, "category": detail.get("primaryGenreName") or detail.get("genre") or "Games",
        "supported_platforms": [label for prefix, label in (("iPhone", "iPhone"), ("iPad", "iPad"),
            ("AppleTV", "Apple TV"), ("AppleVisionPro", "Apple Vision Pro"), ("Mac", "Mac"))
            if any(str(device).startswith(prefix) for device in (detail.get("supportedDevices") or []))] if ios else [],
        "url": f"https://apps.apple.com/us/app/id{record['id']}" if ios else
               "https://play.google.com/store/apps/details?" + urlencode({"id": record["id"], "gl": "US", "hl": "en"}),
    }
    result["media_missing"] = media_missing(result)
    result["media_status"] = "missing" if result["media_missing"] else "complete"
    return result


def apple_candidates(feed, source):
    entries = feed.get("feed", {}).get("entry") or []
    if isinstance(entries, dict):
        entries = [entries]
    rows = {}
    for entry in entries:
        genre = str(entry.get("category", {}).get("attributes", {}).get("im:id", ""))
        if genre != "6014" and not (genre.isdigit() and 7001 <= int(genre) <= 7019):
            continue
        app_id = str(entry.get("id", {}).get("attributes", {}).get("im:id", ""))
        if not app_id.isdigit():
            continue
        rows[app_id] = {"platform": "ios", "id": app_id, "source": source,
                        "name": entry.get("im:name", {}).get("label", ""),
                        "icon": _entry_icon(entry),
                        "candidate_release_date": release_date(entry.get("im:releaseDate", {}).get("label"))}
    return list(rows.values()), feed.get("feed", {}).get("updated", {}).get("label")


def add_candidates(catalog, candidates, timestamp):
    for candidate in candidates:
        key = f"{candidate['platform']}:{candidate['id']}"
        if key not in catalog["apps"]:
            catalog["apps"][key] = {**candidate, "country": "us", "first_discovered_at": timestamp,
                                    "status": "pending", "sources": [candidate["source"]],
                                    "url": f"https://apps.apple.com/us/app/id{candidate['id']}" if candidate["platform"] == "ios" else
                                    "https://play.google.com/store/apps/details?" + urlencode({"id": candidate["id"], "gl": "US", "hl": "en"})}
        elif candidate["source"] not in catalog["apps"][key]["sources"]:
            catalog["apps"][key]["sources"].append(candidate["source"])
        if candidate.get("icon") and not catalog["apps"][key].get("icon"):
            catalog["apps"][key]["icon"] = candidate["icon"]


def collect_source(catalog, source, fetch, timestamp):
    status = catalog["sources"].setdefault(source, {})
    status["attempted_at"] = timestamp
    try:
        rows, updated = fetch()
        add_candidates(catalog, rows, timestamp)
        status.update({"last_success_at": timestamp, "source_updated_at": updated, "candidates": len(rows), "error": None})
        dates = [r["candidate_release_date"] for r in rows if r.get("candidate_release_date") and r["candidate_release_date"] <= timestamp[:10]]
        status["latest_candidate_release_date"] = max(dates, default=None)
    except Exception as error:
        status["error"] = str(error)[:500]


def discover(catalog, data_dir, now):
    timestamp = now.isoformat()
    day = now.astimezone(BEIJING).date().isoformat()
    for kind in ("newapplications", "newfreeapplications", "newpaidapplications"):
        source = f"apple:{kind}"
        url = f"https://itunes.apple.com/us/rss/{kind}/genre=6014/limit=100/json"
        collect_source(catalog, source, lambda url=url, source=source: apple_candidates(
            _fetch_json(url, timeout=15, retries=0), source), timestamp)
    snapshot = load(data_dir / "daily" / f"{day}.json", {})
    for platform in ("ios", "android"):
        free_board = next((board for board in snapshot.get("platforms", {}).get(platform, {}).get("boards", [])
                           if board["label"] == "美区免费榜" and board.get("rows") and not board.get("error")), None)
        if free_board:
            source = "apple:free" if platform == "ios" else "google:free"
            rows = [{"platform": platform, "id": str(row["app_id"] if platform == "ios" else row["package"]),
                     "name": row["name"], "icon": row.get("icon", ""), "source": source} for row in free_board["rows"]]
            collect_source(catalog, source, lambda rows=rows, board=free_board: (
                rows, board.get("scraped_at_utc") or snapshot.get("scraped_at_utc")), timestamp)
    boards = snapshot.get("platforms", {}).get("android", {}).get("boards", [])
    for chart in ("new_free", "new_paid"):
        source = f"google:{chart}"
        previous = catalog["sources"].get(source, {})
        if previous.get("last_success_at") and not previous.get("error"):
            success = datetime.fromisoformat(previous["last_success_at"]).astimezone(BEIJING)
            if success.date().isoformat() == day:
                continue
        def fetch(chart=chart, source=source):
            board = next((b for b in boards if b["label"] == googleplay.CHARTS[chart]
                          and b.get("rows") and not b.get("error")), None)
            if board is None:
                board = googleplay.scrape(charts=[chart], log=lambda _: None)["boards"][0]
            if board.get("error"):
                raise ValueError(board["error"])
            return [{"platform": "android", "id": row["package"], "name": row["name"], "source": source, "icon": row.get("icon", "")}
                    for row in board["rows"]], None
        collect_source(catalog, source, fetch, timestamp)


def fetch_detail(record):
    if record["platform"] == "android":
        return googleplay.fetch_details(record["id"], timeout=15)
    query = urlencode({"id": record["id"], "country": "us", "entity": "software"})
    results = _fetch_json("https://itunes.apple.com/lookup?" + query, timeout=15, retries=0).get("results") or []
    return next((app for app in results if str(app.get("trackId")) == record["id"]), None)


def pending_targets(catalog, now):
    today = now.astimezone(BEIJING).date().isoformat()
    queues = {(platform, paid): deque() for platform in ("ios", "android") for paid in (False, True)}
    for record in sorted(catalog["apps"].values(), key=lambda r: (
            all(source in ("apple:free", "google:free") for source in r["sources"]),
            r.get("last_attempt_at", ""), r["first_discovered_at"])):
        status = record["status"]
        needs_media = record.get("media_status") != "complete"
        needs_video = record["platform"] == "android" and bool(record.get("video_error")) and record.get("video_attempted_at", "") < (now - timedelta(minutes=30)).isoformat()
        if status == "not_game" or (status == "verified" and not needs_media and not needs_video):
            continue
        if status == "scheduled" and record.get("release_date", "") > today and not needs_media and not needs_video:
            continue
        if status != "pending" and needs_media and record.get("media_checked_at", "")[:10] == now.date().isoformat():
            continue
        if status in ("undated", "unavailable") and record.get("last_attempt_at", "")[:10] == now.date().isoformat():
            continue
        paid = any(source.endswith(("new_paid", "newpaidapplications")) for source in record["sources"])
        queues[record["platform"], paid].append(record)
    selected = []
    while any(queues.values()) and len(selected) < 40:
        for queue in queues.values():
            if queue and len(selected) < 40:
                selected.append(queue.popleft())
    return selected


def run(data_dir, now=None, log=print):
    now = now or datetime.now(timezone.utc)
    deadline = time.monotonic() + 450
    day = now.astimezone(BEIJING).date().isoformat()
    directory = data_dir / "releases"
    path = directory / "catalog.json"
    catalog = load(path, {"schema_version": 1, "apps": {}, "sources": {}})
    catalog["sources"].pop("discovery", None)
    try:
        discover(catalog, data_dir, now)
    except Exception as error:
        catalog["sources"]["discovery"] = {"attempted_at": now.isoformat(), "error": str(error)[:500]}
    save(path, catalog)
    store = load(data_dir / "base/store-apps.json", {})
    selected = pending_targets(catalog, now)
    attempted = 0
    store_dirty = False
    def verify(record):
        if time.monotonic() >= deadline - 35:
            return None
        key = f"{record['platform']}:{record['id']}"
        store_key = f"ios:us:{record['id']}" if record["platform"] == "ios" else key
        known = store.get(store_key, {})
        cached = dict(known.get("release_info") or {})
        if cached:
            cached.update({field: known[field] for field in ("description", "version") if known.get(field)})
            if record["platform"] == "ios":
                cached.update({field: known[source] for field, source in (("screenshotUrls", "screenshots"), ("artworkUrl512", "icon")) if known.get(source)})
            else:
                cached.update({field: known[field] for field in ("screenshots", "icon") if known.get(field)})
            # Older cache entries kept only release fields, not the full store response.
            if media_missing({"description": cached.get("description"), "version": cached.get("version"),
                              "icon": cached.get("artworkUrl512") or cached.get("icon"),
                              "screenshots": cached.get("screenshotUrls") or cached.get("ipadScreenshotUrls") or cached.get("appletvScreenshotUrls") or cached.get("screenshots"),
                              "publisher": cached.get("sellerName") or cached.get("artistName") or cached.get("developer")}):
                cached = None
        identity_key, date_key = ("trackId", "releaseDate") if record["platform"] == "ios" else ("appId", "released")
        if cached and (str(cached.get(identity_key)) != record["id"] or not cached.get(date_key)
                       or not (cached.get("genreId") or cached.get("genreIds") or cached.get("primaryGenreId"))):
            cached = None
        try:
            detail = cached if cached else fetch_detail(record)
            result = assess(record, detail, now)
            if cached and "metrics_checked_at" in result:
                # Cached details retain their original observation date.
                result["metrics_checked_at"] = known.get("metrics_checked_at") or known.get("details_checked_on") or known.get("checked_on") or ""
            if result["status"] in ("verified", "scheduled", "undated"):
                if record["platform"] == "ios":
                    video_cache = {**known, **record}
                    if video_cache.get("video_checked_at", "")[:10] > (now - timedelta(days=7)).date().isoformat():
                        result.update({field: video_cache[field] for field in ("videos", "video_checked_at", "video_attempted_at", "video_error") if field in video_cache})
                    # Apple video collection is paused; keep previously saved URLs only.
                    if "videos" not in result and video_cache.get("videos"):
                        result["videos"] = video_cache["videos"]
                elif cached and known.get("video_checked_at"):
                    result["video_checked_at"] = known["video_checked_at"]
                for field in ("description", "version", "icon", "screenshots", "publisher", "last_updated_date"):
                    previous_value = record.get(field) or known.get(field) or (known.get("developer") if field == "publisher" else None)
                    if not result.get(field) and previous_value:
                        result[field] = previous_value
                result["media_missing"] = media_missing(result)
                result["media_status"] = "missing" if result["media_missing"] else "complete"
                result["media_checked_at"] = now.isoformat()
                result["_store"] = {
                    **{key: result[key] for key in ("videos", "video_checked_at", "video_attempted_at", "video_error") if key in result},
                    **{key: result[key] for key in ("rating", "rating_count", "review_count", "installs", "metrics_checked_at", "last_updated_date") if key in result},
                    **{key: result[key] for key in ("name", "description", "version", "icon", "screenshots", "category",
                         "release_date", "free", "price", "currency", "media_status", "media_missing", "media_checked_at", "supported_platforms")},
                    "platform": record["platform"], "country": "us", "id": record["id"], "url": result["url"],
                    "publisher": result["publisher"], "developer": result["publisher"],
                    "release_info": {key: detail.get(key) for key in (("trackId", "trackName", "genreIds", "primaryGenreId", "releaseDate", "currentVersionReleaseDate", "price", "currency", "sellerName", "artistName", "artworkUrl512", "supportedDevices", *METRIC_FIELDS)
                        if record["platform"] == "ios" else ("appId", "title", "genreId", "released", "updated", "developer", "free", "price", "currency", "icon", *METRIC_FIELDS, *VIDEO_FIELDS))},
                }
            return {**result, "last_attempt_at": now.isoformat(), "checked_at": now.isoformat(), "error": None}
        except Exception as error:
            return {"last_attempt_at": now.isoformat(), "error": str(error)[:500]}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(verify, record): record for record in selected}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                attempted += 1
                cached_record = result.pop("_store", None)
                if cached_record:
                    key = f"ios:us:{cached_record['id']}" if cached_record["platform"] == "ios" else f"android:{cached_record['id']}"
                    previous = store.setdefault(key, {})
                    names = set(previous.get("names", [])) | {cached_record["name"]}
                    if previous.get("name"):
                        names.add(previous["name"])
                    previous.update({field: value for field, value in cached_record.items() if value is not None and value != "" and value != []})
                    previous["media_missing"] = cached_record["media_missing"]
                    if "video_error" in cached_record:
                        previous["video_error"] = cached_record["video_error"]
                    previous["names"] = sorted(names)
                    store_dirty = True
                futures[future].update(result)
                save(path, catalog)
    if store_dirty:
        save(data_dir / "base/store-apps.json", store)
    snapshot = {"date_beijing": day, "scraped_at_utc": now.isoformat(), "country": "us",
                "coverage": "官方美区榜单与新应用候选，覆盖有限，不保证枚举全部新游戏；发布日期不代表美国区首次开放日期。",
                "verification": {"selected": len(selected), "attempted": attempted, "limit": 40},
                "basic_media": {"total": len(catalog["apps"]), "complete": sum(r.get("media_status") == "complete" for r in catalog["apps"].values())},
                "sources": catalog["sources"], "apps": [r for r in catalog["apps"].values()
                    if not all(source in ("apple:free", "google:free") for source in r["sources"])]}
    save(directory / f"{day}.json", snapshot)
    catalog["updated_at"] = datetime.now(timezone.utc).isoformat()
    save(path, catalog)
    for source, status in catalog["sources"].items():
        log(f"[上架来源/{source}] {status.get('error') or str(status.get('candidates', 0)) + ' 款候选'}")
    log(f"[上架监控] {len(catalog['apps'])} 款候选，本次核验 {attempted}/{len(selected)} 款；写出 {directory / (day + '.json')}")
    return snapshot


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    run(args.data_dir)

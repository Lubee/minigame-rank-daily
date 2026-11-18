#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给工作清单里的游戏补「官方媒体与硬信息」。

## 为什么走 iTunes Search API

一次请求同时拿到多个我们需要的硬信号，而且是**官方口径**：

    artworkUrl512   官方图标（可直接高清展示）
    screenshotUrls  5~8 张**真实实机截图**（App Store 商店页同款）
    description     官方玩法介绍 —— 核心玩法/爽点的一手材料
    sellerName      开发商全称
    fileSizeBytes   安装包体积 —— **素材量级最硬的代理指标**
                    50MB 和 500MB 是完全两个量级的美术工程量
    minimumOsVersion / releaseDate / averageUserRating / userRatingCount

对小游戏来说这是目前唯一「无需登录、不限流、字段齐全」的公开源。
微信/抖音小游戏没有公开截图接口，但其热门产品多数在 App Store 也有包，
按名字查一次就能命中一部分。

## 命中判定

游戏名与商店名常不一致（「羊了个羊」vs「羊了个羊：星球」），所以做归一化后
按 精确 > 包含 > 模糊 打分，低于阈值的丢弃，避免张冠李戴。
判定结果带 match_score，站点上对低分匹配会标注「待人工确认」。

缓存写到 data/detail/_media.json，**跨次运行合并**（不清空），重跑不重复请求。

用法：
    python scripts/detail/enrich_media.py            # 只补没缓存的
    python scripts/detail/enrich_media.py --force    # 全部重查
    python scripts/detail/enrich_media.py --limit 20 # 本次最多查 20 个
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from schema import norm_name
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
DETAIL_DIR = ROOT / "data" / "detail"
WORKLIST = DETAIL_DIR / "_worklist.json"
MEDIA = DETAIL_DIR / "_media.json"
BEIJING = timezone(timedelta(hours=8))

ITUNES = "https://itunes.apple.com/search"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 归一化时剥掉的噪声后缀（商店名常见修饰）
NOISE = re.compile(
    r"(小游戏|手游|官方版|正版|免费版|手机版|最新版|官方正版|官方|ios版|苹果版)")


def norm(s: str) -> str:
    s = str(s or "").strip().lower()
    s = NOISE.sub("", s)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s)


def similarity(a: str, b: str) -> float:
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    short, long_ = sorted((na, nb), key=len)
    if short in long_ and len(short) / len(long_) >= 0.6:
        # 「羊了个羊」 vs 「羊了个羊星球」
        return round(0.80 + 0.18 * (len(short) / len(long_)), 3)
    return round(difflib.SequenceMatcher(None, na, nb).ratio(), 3)


def upgrade(url: str, spec: str) -> str:
    """把 mzstatic 的尺寸段换掉，取更高清版本。"""
    if not url:
        return url
    return re.sub(r"/\d+x\d+bb\.(jpg|png|webp)$", f"/{spec}bb.jpg", url)


def query_itunes(term: str, country: str, limit: int = 5,
                 timeout: int = 20) -> list[dict]:
    qs = urllib.parse.urlencode({
        "term": term, "country": country, "entity": "software", "limit": limit,
    })
    req = urllib.request.Request(f"{ITUNES}?{qs}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode()).get("results") or []


def pick(name: str, candidates: list[dict], min_score: float) -> dict | None:
    best, best_score = None, 0.0
    for c in candidates:
        s = similarity(name, c.get("trackName") or "")
        if s > best_score:
            best, best_score = c, s
    if not best or best_score < min_score:
        return None
    return {"cand": best, "score": best_score}


def to_record(name: str, cand: dict, score: float, country: str) -> dict:
    shots = [upgrade(u, "626x1112") for u in (cand.get("screenshotUrls") or [])]
    shots += [upgrade(u, "626x1112") for u in (
        cand.get("ipadScreenshotUrls") or [])]
    return {
        "matched": True,
        "match_score": score,
        "match_name": cand.get("trackName") or "",
        "country": country,
        "track_id": cand.get("trackId"),
        "seller": cand.get("sellerName") or cand.get("artistName") or "",
        "genres": cand.get("genres") or [],
        "genre": cand.get("primaryGenreName") or "",
        "icon": upgrade(cand.get("artworkUrl512") or cand.get("artworkUrl100") or "",
                        "512x512"),
        "screenshots": shots,
        "description": (cand.get("description") or "").strip(),
        "release_notes": cand.get("releaseNotes") or "",
        # 接口有时把体积给成字符串（"537141248"），统一成 int
        "file_size_bytes": int(str(cand.get("fileSizeBytes") or 0).strip() or 0),
        "min_os": cand.get("minimumOsVersion") or "",
        "version": cand.get("version") or "",
        "release_date": (cand.get("releaseDate") or "")[:10],
        "rating": cand.get("averageUserRating") or 0,
        "rating_count": cand.get("userRatingCount") or 0,
        "price": cand.get("formattedPrice") or "",
        "advisory": cand.get("contentAdvisoryRating") or "",
        "url": cand.get("trackViewUrl") or "",
        "fetched_at": datetime.now(BEIJING).isoformat(timespec="seconds"),
    }


def identity_media(entry: dict) -> dict:
    """Use source IDs. Name-based cross-platform candidates remain for AI review."""
    regions = {"美区免费榜": "us", "国区免费榜": "cn", "日区免费榜": "jp"}
    store_path = ROOT / "data" / "base" / "store-apps.json"
    store = json.loads(store_path.read_text(encoding="utf-8")) if store_path.exists() else {}
    sources = []
    for path in sorted((ROOT / "data" / "daily").glob("????-??-??.json"), reverse=True):
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        for platform, data in snapshot.get("platforms", {}).items():
            identities = entry.get("platform_app_ids") or {}
            if identities.get("android") and platform != "android":
                continue
            for board in data.get("boards", []):
                for row in board.get("rows", []):
                    row_id = row.get("package") if platform == "android" else row.get("app_id")
                    expected_id = identities.get(platform)
                    if expected_id and str(row_id) != str(expected_id):
                        continue
                    if not expected_id and norm_name(row.get("name")) != norm_name(entry["name"]):
                        continue
                    if row.get("url") and row["url"] not in sources:
                        sources.append(row["url"])
                    if platform == "ios" and row.get("app_id"):
                        country = regions.get(board["label"])
                        if not country:
                            continue
                        query = urllib.parse.urlencode({"id": row["app_id"], "country": country, "entity": "software"})
                        try:
                            req = urllib.request.Request("https://itunes.apple.com/lookup?" + query, headers={"User-Agent": UA})
                            with urllib.request.urlopen(req, timeout=15) as response:
                                candidates = json.load(response).get("results") or []
                            candidate = next((item for item in candidates if str(item.get("trackId")) == str(row["app_id"])), None)
                            if candidate:
                                result = to_record(entry["name"], candidate, 1.0, country)
                                result["identity_match"] = "app_id"
                                return result
                        except (OSError, ValueError) as error:
                            print(f"[media] {entry['name']} ID 查询失败：{type(error).__name__}")
                        known = store.get(f"ios:{country}:{row['app_id']}")
                    elif platform in ("android", "taptap"):
                        app_id = row.get("package") if platform == "android" else row.get("app_id")
                        known = store.get(f"{platform}:{app_id}")
                        cutoff = (datetime.now(BEIJING) - timedelta(days=7)).date().isoformat()
                        if (platform == "android" and app_id
                                and (not known or known.get("details_checked_on", "") <= cutoff)):
                            from scrape_googleplay import fetch_details
                            try:
                                app = fetch_details(app_id)
                                known = {
                                    "name": app["title"], "country": "us", "developer": app.get("developer"),
                                    "icon": app.get("icon") or "", "screenshots": app.get("screenshots") or [],
                                    "description": app.get("description") or "", "url": app.get("url") or row.get("url"),
                                    "version": app.get("version") or "",
                                }
                            except (OSError, ValueError, subprocess.SubprocessError) as error:
                                print(f"[media] {entry['name']} Google Play 查询失败：{error}")
                    else:
                        continue
                    if known:
                        return {
                            "matched": True, "identity_match": "package" if platform == "android" else "app_id",
                            "platform": platform, "package": app_id if platform == "android" else None,
                            "match_name": known["name"], "match_score": 1.0,
                            "country": known.get("country"), "seller": known.get("developer") or known.get("publisher"),
                            "icon": known.get("icon"), "screenshots": known.get("screenshots") or [],
                            "description": known.get("description") or "", "url": known.get("url"),
                            "version": known.get("version") or "", "file_size_bytes": 0,
                            "fetched_at": datetime.now(BEIJING).isoformat(timespec="seconds"),
                        }
        # A single source record is sufficient; do not repeat missing-ID lookups across historical days.
        if sources:
            break
    return {
        "matched": False, "source_urls": sources,
        "icon": entry.get("icon_url") or "", "screenshots": [],
        "fetched_at": datetime.now(BEIJING).isoformat(timespec="seconds"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="忽略缓存重查")
    ap.add_argument("--limit", type=int, default=0, help="本次最多查询数")
    ap.add_argument("--delay", type=float, default=1.4, help="请求间隔秒")
    ap.add_argument("--min-score", type=float, default=0.74)
    ap.add_argument("--names-json", help="仅查询指定目标的 JSON 名称数组，优先按应用身份取资料")
    args = ap.parse_args()

    if not WORKLIST.exists():
        raise SystemExit("先跑 build_worklist.py")
    wl = json.loads(WORKLIST.read_text(encoding="utf-8"))
    entries = wl.get("entries") or []
    selected = json.loads(args.names_json) if args.names_json is not None else None
    if selected is not None:
        if not isinstance(selected, list) or not all(isinstance(name, str) for name in selected):
            ap.error("--names-json 必须为字符串数组")
        selected = set(selected)
        entries = [entry for entry in entries if entry["name"] in selected]

    cache: dict[str, dict] = {}
    if MEDIA.exists():
        try:
            cache = (json.loads(MEDIA.read_text(encoding="utf-8"))
                     or {}).get("games") or {}
        except Exception:  # noqa: BLE001
            cache = {}

    todo = [e for e in entries
            if selected is not None or args.force or e["name"] not in cache]
    if args.limit:
        todo = todo[:args.limit]
    print(f"工作清单 {len(entries)} 款｜已缓存 {len(cache)}｜本次查询 {len(todo)}")

    hit = miss = fail = 0
    for i, e in enumerate(todo, 1):
        name = e["name"]
        rec = None
        if selected is not None:
            rec = identity_media(e)
        for country in (() if selected is not None else ("cn", "us")):
            try:
                cands = query_itunes(name, country)
            except Exception as ex:  # noqa: BLE001
                print(f"  [{i}/{len(todo)}] {name} :: 请求失败 {type(ex).__name__}")
                fail += 1
                time.sleep(args.delay * 2)
                break
            got = pick(name, cands, args.min_score)
            if got:
                rec = to_record(name, got["cand"], got["score"], country)
                break
            time.sleep(args.delay * 0.5)
        if rec and rec.get("matched"):
            cache[name] = rec
            hit += 1
            print(f"  [{i}/{len(todo)}] {name} -> {rec['match_name']}"
                  f"（{rec['match_score']}）截图{len(rec['screenshots'])}张"
                  f" {rec['file_size_bytes']//1048576}MB")
        else:
            if rec is not None:
                cache[name] = rec
            elif name not in cache:
                cache[name] = {"matched": False, "fetched_at":
                               datetime.now(BEIJING).isoformat(timespec="seconds")}
            miss += 1
            print(f"  [{i}/{len(todo)}] {name} -> 未命中")
        if i % 10 == 0 or i == len(todo):
            MEDIA.write_text(json.dumps(
                {"updated_at": datetime.now(BEIJING).isoformat(timespec="seconds"),
                 "games": cache}, ensure_ascii=False, indent=1), encoding="utf-8")
        time.sleep(args.delay)

    MEDIA.write_text(json.dumps(
        {"updated_at": datetime.now(BEIJING).isoformat(timespec="seconds"),
         "games": cache}, ensure_ascii=False, indent=1), encoding="utf-8")

    total_hit = sum(1 for v in cache.values() if v.get("matched"))
    with_shots = sum(1 for v in cache.values() if v.get("screenshots"))
    print(f"\n本次：命中 {hit}｜未命中 {miss}｜请求失败 {fail}")
    print(f"缓存累计：{len(cache)} 款，命中 {total_hit}，其中 {with_shots} 款有实机截图")
    print(f"已写 {MEDIA.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

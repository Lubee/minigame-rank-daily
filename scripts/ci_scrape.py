"""
CI scrape entry: invoked by GitHub Actions.

Tries the Playwright storage state from $GRAVITY_AUTH first. For today's
rankings, falls back to anonymous scraping (Top 20) if login fails.

Writes:
  data/daily/<YYYY-MM-DD>.json   one snapshot per UTC date
  data/latest.json               always the most recent snapshot
  data/history.jsonl             append-only log of (date, board, totals)

The snapshot JSON is the same structure as scrape_rank.do_scrape but
flattened a little to be web-friendly.

Historical re-pull: set GRAVITY_DATE=YYYY-MM-DD to re-scrape a specific
past day's ranking via the site's date picker (requires a logged-in
session). The output lands in data/daily/<that date>.json and
overwrites any existing file only when GRAVITY_FORCE is also set, so
re-pulling a committed snapshot is always explicit.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent  # repo root
sys.path.insert(0, str(HERE))

import scrape_rank as core  # noqa: E402


# Beijing time (UTC+8) — we want the date label to match Chinese users.
BEIJING_TZ = timezone(timedelta(hours=8))


def resolve_auth(log) -> Path | None:
    """Materialize the auth file from $GRAVITY_AUTH (raw JSON) or from
    a path in $GRAVITY_AUTH_FILE. Returns the file path, or None for
    anonymous mode."""
    raw = os.environ.get("GRAVITY_AUTH", "").strip()
    if raw:
        # Could be a JSON blob, or a path. Heuristic: starts with `{`?
        tmp = ROOT / "_auth_runtime.json"
        if raw.lstrip().startswith("{"):
            tmp.write_text(raw, encoding="utf-8")
            log("使用 $GRAVITY_AUTH 中的 JSON 内容作为登录态")
            return tmp
        # Treat as path
        p = Path(raw)
        if p.is_file():
            log(f"使用登录态文件 {p}")
            return p
        log(f"$GRAVITY_AUTH 看起来不是 JSON 也不是有效路径，匿名运行")
        return None
    log("未设置 $GRAVITY_AUTH，匿名模式（Top 20）")
    return None


def supplement_googleplay(out_path, log):
    """Repair today's missing Android chart without changing other platforms."""
    data = json.loads(out_path.read_text(encoding="utf-8"))
    boards = data.get("platforms", {}).get("android", {}).get("boards", [])
    if any(b.get("rows") and not b.get("error") for b in boards):
        log(f"快照 {out_path.name} 已有 Google Play 榜单，跳过")
        return
    from scrape_googleplay import scrape
    fragment = scrape(top_n=100, log=log)
    board = fragment["boards"][0]
    if board.get("error") or not board.get("rows"):
        log("[android] 补采失败，保留已有快照")
        return
    data.setdefault("platforms", {})["android"] = fragment
    day = out_path.stem
    documents = {}
    latest = ROOT / "data" / "latest.json"
    if latest.exists() and json.loads(latest.read_text(encoding="utf-8")).get("date_beijing") == day:
        documents[latest] = json.dumps(data, ensure_ascii=False, indent=2)
    history = ROOT / "data" / "history.jsonl"
    if history.exists():
        summaries = [json.loads(line) for line in history.read_text(encoding="utf-8").splitlines() if line.strip()]
        for summary in summaries:
            if summary.get("date") == day:
                summary.setdefault("boards", {})["android/美区免费榜"] = len(board["rows"])
        documents[history] = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in summaries)
    # Write the daily snapshot last so an interrupted supplement can be retried.
    documents[out_path] = json.dumps(data, ensure_ascii=False, indent=2)
    for path, content in documents.items():
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as output:
                temporary = Path(output.name)
                output.write(content)
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    log(f"[android] 已补采 {len(board['rows'])} 条，更新当天快照与趋势")
    try:
        from store_apps import update_store_apps
        update_store_apps({"platforms": {"android": fragment}}, ROOT / "data", day, log)
    except Exception as e:
        log(f"[商店资料] 更新失败，保留当天榜单：{e}")


def scrape_gravity(auth, top_n, daily_dir, hist_date, log):
    def scrape_browser(auth_file, limit):
        data = core.do_scrape(
            top_n=limit,
            force_anon=auth_file is None,
            out_dir=daily_dir,
            auth_file=auth_file or core.DEFAULT_AUTH_FILE,
            log=log,
            historical_date=hist_date,
        )
        data["source_mode"] = "playwright"
        return data

    if auth:
        log("[引力引擎] 优先使用登录态抓取")
        try:
            data = scrape_browser(auth, top_n)
        except Exception as e:
            log(f"[引力引擎] 登录抓取失败（{type(e).__name__}）")
        else:
            counts = {
                (platform, board["label"]): len(board.get("rows", []))
                for platform, result in data.get("platforms", {}).items()
                for board in result.get("boards", [])
            }
            expected = [(platform, label) for platform, labels in core.BOARD_LABELS.items()
                        for label in labels]
            if data.get("auth_expired") or not data.get("logged_in"):
                log("[引力引擎] 登录态不可用")
            elif not all(counts.get(board, 0) for board in expected):
                log("[引力引擎] 登录抓取未取得完整六榜")
            elif not hist_date and top_n > 20 and max(counts[board] for board in expected) <= 20:
                log("[引力引擎] 登录抓取未超过匿名条数上限")
            else:
                return data

    if hist_date:
        raise SystemExit("[引力引擎] 历史补抓需要有效登录态和完整榜单，保留原快照。")

    anonymous_limit = min(top_n, 20)
    try:
        import scrape_gravity_http as ghttp
        log("[引力引擎] 使用匿名 HTTP 接口")
        data = ghttp.do_scrape(top_n=anonymous_limit, daily_dir=daily_dir, log=log)
        data["source_mode"] = "gravity_http"
        return data
    except Exception as e:
        log(f"[引力引擎] 匿名 HTTP 失败，回落匿名 Playwright（{type(e).__name__}）")

    return scrape_browser(None, anonymous_limit)


def main():
    log = print

    daily_dir = ROOT / "data" / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)

    # Optional historical re-pull: GRAVITY_DATE=YYYY-MM-DD drives the site's
    # date picker to that day instead of scraping "today". Requires a
    # logged-in session (anonymous mode only exposes today). Used to
    # re-pull a past date whose snapshot was bad.
    hist_date = os.environ.get("GRAVITY_DATE", "").strip() or None

    # We use Beijing date for the filename so a daily 03:33 BJT run lands
    # cleanly into "today's" file.
    now_bj = datetime.now(BEIJING_TZ)
    day_label = hist_date or now_bj.strftime("%Y-%m-%d")
    out_path = daily_dir / f"{day_label}.json"

    # Keep the first daily snapshot; only repair a missing Google Play chart.
    # The Gravity Engine site occasionally rewrites its own rankings
    # later in the day (e.g. rank 89 changes at 12:00), so subsequent
    # scrapes would overwrite the initial "correct" 10:30 data. Force
    # a re-scrape by deleting today's daily/*.json before running.
    # A historical re-pull (GRAVITY_DATE set) is inherently intentional,
    # so it implies force — overwriting that past snapshot is the point.
    force = bool(hist_date) or os.environ.get("GRAVITY_FORCE", "").lower() in ("1", "true", "yes")
    if out_path.exists() and not force:
        supplement_googleplay(out_path, log)
        return

    auth = resolve_auth(log)
    top_env = os.environ.get("GRAVITY_TOP_N", "").strip()
    top_n = int(top_env) if top_env else (100 if auth else 20)

    # Gravity Engine (wx/douyin) is the primary source, but a failure here
    # must NOT block the auxiliary sources below (they may succeed while gravity-engine
    # times out — e.g. a transient outage or a slow day). Degrade to a
    # partial snapshot so iOS/TapTap data still lands in the daily file.
    try:
        data = scrape_gravity(auth, top_n, daily_dir, hist_date, log)
    except Exception as e:
        log(f"[引力引擎] 抓取失败，继续采集独立数据源（{type(e).__name__}）")
        data = {
            "scraped_at": datetime.now().isoformat(timespec="seconds"),
            "period": "日榜",
            "logged_in": False,
            "top_n_target": top_n,
            "source": core.URL,
            "platforms": {},
            "gravity_error": str(e),
        }

    # Tag with absolute and Beijing timestamps for the UI.
    data["scraped_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data["scraped_at_beijing"] = now_bj.isoformat(timespec="seconds")
    data["date_beijing"] = day_label

    # Merge auxiliary platforms (separate data sources — see scrape_taptap.py / scrape_ios.py /
    # scrape_googleplay.py):
    #   taptap   -> TapTap 预约榜 (SSR JSON-LD)
    #   ios      -> App Store 美/国/日区游戏免费榜 (Apple 官方 iTunes RSS)
    #   android  -> Google Play 美区免费游戏榜 (Node.js RPC adapter)
    # Wrapped so a failure never blocks the gravity-engine snapshot for
    # wx/douyin.
    #
    # These sources expose no historical API — they always return the
    # current board. For a historical re-pull we must NOT fold today's
    # data into a past day's snapshot; reuse the boards already stored
    # in the file we're overwriting (if any), else omit them.
    if hist_date and out_path.exists():
        try:
            prev = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception as e:
            prev = {}
            log(f"读取旧快照失败，历史重拉将不含辅助平台: {e}")
        for pkey, blabel in (("taptap", "预约榜"), ("ios", "免费榜"),
                             ("android", "免费榜")):
            prev_frag = prev.get("platforms", {}).get(pkey)
            if prev_frag:
                data.setdefault("platforms", {})[pkey] = prev_frag
                n_rows = sum(len(b.get("rows", []))
                             for b in prev_frag.get("boards", []))
                log(f"复用旧快照 {pkey}/{blabel}: {n_rows} 条（历史重拉）")
            else:
                log(f"[{pkey}] 旧快照无此平台，历史重拉将不含 {pkey}")
    elif not hist_date:
        for pkey, modname, blabel in (
            ("taptap", "scrape_taptap", "预约榜"),
            ("ios", "scrape_ios", "免费榜"),
            ("android", "scrape_googleplay", "免费榜"),
        ):
            try:
                mod = __import__(modname)
                frag = mod.scrape(top_n=100 if pkey == "android" else top_n, log=log)
                data.setdefault("platforms", {})[pkey] = frag
                n_rows = sum(len(b.get("rows", []))
                             for b in frag.get("boards", []))
                log(f"合并 {pkey}/{blabel}: {n_rows} 条")
            except Exception as e:
                log(f"[{pkey}] 抓取失败，跳过（不影响其他平台）: {e}")

    out_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(f"写出 {out_path}")

    # The latest.json pointer and history.jsonl trend log track the
    # advancing frontier of daily snapshots. A historical re-pull of a
    # past date must not regress latest.json back to that date or append
    # a duplicate trend line — only the normal "today" run updates them.
    if not hist_date:
        latest = ROOT / "data" / "latest.json"
        latest.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log(f"更新 {latest}")

        histfile = ROOT / "data" / "history.jsonl"
        summary = {
            "date": day_label,
            "scraped_at_utc": data["scraped_at_utc"],
            "logged_in": data.get("logged_in", False),
            "boards": {},
        }
        for plat_key, plat in data.get("platforms", {}).items():
            for b in plat.get("boards", []):
                key = f"{plat_key}/{b['label']}"
                summary["boards"][key] = len(b.get("rows", []))
        with histfile.open("a", encoding="utf-8") as f:
            f.write(json.dumps(summary, ensure_ascii=False) + "\n")
        log(f"追加 {histfile}")

        try:
            from store_apps import update_store_apps
            update_store_apps(data, ROOT / "data", day_label, log)
        except Exception as e:
            log(f"[商店资料] 更新失败，保留已有资料与当天榜单：{e}")

    # Don't keep the per-snapshot xlsx that core wrote alongside;
    # the web UI reads JSON, and xlsx files would bloat the repo fast.
    for ext in (".xlsx",):
        for f in daily_dir.glob(f"rank_*{ext}"):
            try:
                f.unlink()
            except Exception:
                pass
    # Same for the redundant rank_*.json that core writes.
    for f in daily_dir.glob("rank_*.json"):
        try:
            f.unlink()
        except Exception:
            pass

    # Clean up the runtime auth blob if we created one.
    runtime_auth = ROOT / "_auth_runtime.json"
    if runtime_auth.exists():
        try:
            runtime_auth.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    main()

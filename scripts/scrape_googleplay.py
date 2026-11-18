"""Google Play US top-free games via the pinned Node.js scraper.

fetch_media retains public listing-image support for store_apps.py.
"""
from __future__ import annotations

import html as html_mod
import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Callable

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_PLAY_ICON_RE = re.compile(r'<meta property="og:image" content="([^"]+)"')
_PLAY_SCREENSHOT_RE = re.compile(r'<img\s+src="([^"]+)"[^>]*\bdata-screenshot-index="\d+"')


def fetch_media(store_url: str) -> dict:
    """Extract original Google Play image URLs from the public listing."""
    html = _fetch(store_url + "&hl=en", timeout=6, retries=0)
    media = {}
    icon = _PLAY_ICON_RE.search(html)
    if icon and icon[1].startswith("https://play-lh.googleusercontent.com/"):
        media["icon"] = html_mod.unescape(icon[1])
    media["screenshots"] = list(dict.fromkeys(
        html_mod.unescape(url) for url in _PLAY_SCREENSHOT_RE.findall(html)
        if url.startswith("https://play-lh.googleusercontent.com/")
    ))
    return media


def _fetch(url: str, timeout: float = 25, retries: int = 3) -> str:
    """GET with a browser UA; retry on transient errors / 429 with backoff."""
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept-Language": "en-US,en;q=0.9",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last_err = e
            wait = 3 * (attempt + 1)  # 3s, 6s, 9s, 12s
            if attempt < retries:
                time.sleep(wait)
            else:
                raise
    raise last_err  # type: ignore[misc]


def _run_scraper(*args: str, timeout: int = 45):
    # Local Windows jobs may still have Node 18 on PATH; CI uses setup-node.
    local_node = Path(__file__).resolve().parents[1] / ".runtime" / "node.exe"
    result = subprocess.run(
        [str(local_node) if local_node.is_file() else "node", "--use-env-proxy", "--use-system-ca",
         str(Path(__file__).with_name("googleplay_chart.mjs")), *args],
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=timeout,
    )
    return json.loads(result.stdout)


def fetch_details(package: str, timeout: int = 25) -> dict:
    """Fetch official listing material only for a selected research target."""
    if not re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+", package):
        raise ValueError("无效 Google Play 包名")
    try:
        app = _run_scraper("--app", package, str(timeout * 1000), timeout=timeout + 5)
    except subprocess.CalledProcessError as error:
        raise ValueError((error.stderr or str(error)).strip()[:500]) from error
    if not isinstance(app, dict) or app.get("appId") != package or not app.get("title"):
        raise ValueError("Google Play 详情缺失或包名不匹配")
    return app


CHARTS = {"free": "美区免费榜", "new_free": "美区免费新游榜", "new_paid": "美区付费新游榜"}


def scrape(top_n: int = 100, log: Callable = print, charts: list[str] | None = None) -> dict:
    return {"label": "Android", "boards": [
        scrape_board(chart, top_n, log) for chart in (charts if charts is not None else ["free", "new_free"])
    ]}


def scrape_board(chart: str, top_n: int, log: Callable) -> dict:
    """Scrape up to `top_n` entries of the US free GAMES chart.

    Returns the standard snapshot fragment (one board). On fetch/parse
    failure the board is emitted empty with an `error` note so ci_scrape's
    merge never blocks the rest of the snapshot.
    """
    try:
        label = CHARTS[chart]
        if not isinstance(top_n, int) or not 1 <= top_n <= 100:
            raise ValueError("Google Play 条数必须为 1–100")
        apps = _run_scraper(str(top_n), chart)
        if not isinstance(apps, list) or (not apps and chart != "new_paid") or len(apps) > top_n:
            raise ValueError("Google Play 返回空榜或无效榜单")
        packages = set()
        rows = []
        for rank, app in enumerate(apps, 1):
            package = app.get("appId")
            name = app.get("title")
            publisher = app.get("developer")
            if (not isinstance(package, str)
                    or not re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+", package)
                    or not isinstance(name, str) or not name.strip()
                    or not isinstance(publisher, str) or not publisher.strip()):
                raise ValueError(f"Google Play 第 {rank} 条缺少有效包名、名称或开发者")
            if package in packages:
                raise ValueError(f"Google Play 包名重复：{package}")
            packages.add(package)
            rows.append({
                "rank": rank, "name": name.strip(), "package": package,
                "publisher": publisher.strip(), "category": "Games",
                "url": f"https://play.google.com/store/apps/details?id={package}&gl=US&hl=en",
                "icon": app.get("icon", ""),
                "free": app.get("free"), "price": app.get("price"), "currency": app.get("currency"),
            })
    except Exception as e:  # noqa: BLE001
        error = str(e)
        if isinstance(e, subprocess.CalledProcessError):
            error = (e.stderr or error).strip()[:500]
        log(f"[android/{chart}] 抓取失败: {error}")
        return {"label": CHARTS.get(chart, chart), "source": "google_play", "rows": [], "error": error}
    log(f"[android] {label}: {len(rows)} 条")
    return {"label": label, "source": "google_play", "rows": rows,
            "scraped_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    frag = scrape(top_n=n)
    print(json.dumps(frag, ensure_ascii=False, indent=2))
    for b in frag["boards"]:
        print(f"\n{b['label']}: {len(b['rows'])} rows", flush=True)

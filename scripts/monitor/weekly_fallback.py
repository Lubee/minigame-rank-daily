"""Guard automatic weekly runs and recover missing Monday reports after daily runs."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BEIJING = timezone(timedelta(hours=8))
WAIT_SECONDS = 25 * 60


def day_start(now: datetime) -> datetime:
    return now.astimezone(BEIJING).replace(hour=0, minute=0, second=0, microsecond=0)


class GitHub:
    def __init__(self):
        self.token = os.environ["GH_TOKEN"]
        self.base = (os.environ.get("GITHUB_API_URL", "https://api.github.com")
                     + "/repos/" + os.environ["GITHUB_REPOSITORY"])

    def request(self, path: str, data: dict | None = None):
        body = json.dumps(data).encode() if data is not None else None
        req = Request(self.base + path, data=body, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "weekly-report-recovery",
        })
        with urlopen(req, timeout=30) as response:
            raw = response.read()
        return json.loads(raw) if raw else None

    def pages(self, path: str, key: str, params: dict | None = None):
        page = 1
        while True:
            query = urlencode({**(params or {}), "per_page": 100, "page": page})
            result = self.request(path + "?" + query)
            items = result[key]
            if not isinstance(items, list):
                raise ValueError(f"Invalid GitHub response: {key}")
            yield from items
            if len(items) < 100:
                break
            page += 1

    def runs(self, start: datetime):
        since = start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return list(self.pages("/actions/workflows/weekly.yml/runs", "workflow_runs", {
            "branch": "main", "created": ">=" + since,
        }))

    def report_completed(self, run: dict) -> bool:
        # A guard-only skip can also make the workflow green; it is not a delivery.
        if run["conclusion"] != "success":
            return False
        attempt = run.get("run_attempt", 1)
        jobs = self.pages(
            f"/actions/runs/{run['id']}/attempts/{attempt}/jobs", "jobs")
        return any(j["name"] == "report" and j["conclusion"] == "success" for j in jobs)


def inspect_runs(api, start: datetime, exclude_id: int):
    active = []
    for run in api.runs(start):
        if run["id"] == exclude_id:
            continue
        print(f"运行 {run['id']}: {run['status']}/{run.get('conclusion')} "
              f"{run['html_url']}")
        if run["status"] == "completed":
            if api.report_completed(run):
                return True, []
        else:
            active.append(run)
    return False, active


def recover(api, start: datetime, exclude_id: int):
    deadline = time.monotonic() + WAIT_SECONDS
    previous_ids = {run["id"] for run in api.runs(start)}
    while True:
        done, active = inspect_runs(api, start, exclude_id)
        if done:
            print("当天周报已完成，跳过补发。")
            return
        if not active:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("等待已有周报超时；未再次触发，留待下次日报检查。")
        print("已有周报正在排队或执行，30 秒后检查结果。")
        time.sleep(30)

    # If another run starts now, weekly workflow concurrency and its guard deduplicate.
    api.request("/actions/workflows/weekly.yml/dispatches", {
        "ref": "main", "inputs": {"automatic": True},
    })
    print("已请求自动补发：weekly.yml，main，automatic=true。")
    # Dispatch returns no run ID. Find its run link without sending another request.
    for _ in range(12):
        for run in api.runs(start):
            if run["id"] not in previous_ids and run["event"] == "workflow_dispatch":
                print(f"新建周报运行（automatic 输入可在运行中确认）：{run['html_url']}")
                return
        time.sleep(5)
    raise TimeoutError("补发请求已接受，但尚未查到运行链接；请在 Actions 查看，勿重复触发。")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("guard", "recover"))
    args = parser.parse_args()
    now = datetime.now(BEIJING)
    start = day_start(now)
    print(f"北京时间 {now.isoformat()}，查询范围 {start.isoformat()} 至今，分支 main。")
    if args.mode == "recover" and now.weekday() != 0:
        print("今天不是北京时间周一，跳过补发检查。")
        return 0
    if args.mode == "guard" and os.environ.get("AUTOMATIC") != "true":
        should_run = True
        print("人工运行，允许主动重发。")
    else:
        api = GitHub()
        exclude_id = int(os.environ["GITHUB_RUN_ID"])
        if args.mode == "recover":
            recover(api, start, exclude_id)
            return 0
        done, _ = inspect_runs(api, start, exclude_id)
        should_run = not done
        print("当天周报已完成，跳过生成和推送。" if done else "当天尚无成功周报，继续执行。")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"should_run={str(should_run).lower()}\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        # Never log request headers or token values on API failures.
        print(f"::error::周报补发检查失败：{type(error).__name__}；未自动忽略错误。", file=sys.stderr)
        sys.exit(1)

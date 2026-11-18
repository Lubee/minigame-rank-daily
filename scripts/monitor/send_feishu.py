#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Send the weekly report to a Feishu custom bot webhook."""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import send_wecom as report_sender

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MAX_BYTES = 20 * 1024
EXIT_OK = 0
EXIT_BADCONFIG = 2
EXIT_SENDFAIL = 3


def make_sign(timestamp: str, secret: str) -> str:
    to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(to_sign, b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def build_payload(content: str, secret: str, timestamp: str | None = None) -> dict:
    timestamp = timestamp or str(int(time.time()))
    return {
        "timestamp": timestamp,
        "sign": make_sign(timestamp, secret),
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "config": {"update_multi": False},
            "body": {
                "direction": "vertical",
                "elements": [{"tag": "markdown", "content": content}],
            },
        },
    }


def response_succeeded(response: dict) -> bool:
    return (isinstance(response, dict)
            and response.get("code", response.get("StatusCode")) == 0)


def valid_webhook(webhook: str) -> bool:
    parsed = urlsplit(webhook)
    return (parsed.scheme == "https" and parsed.hostname == "open.feishu.cn"
            and parsed.path.startswith("/open-apis/bot/v2/hook/")
            and bool(parsed.path.removeprefix("/open-apis/bot/v2/hook/")))


def post_message(webhook: str, secret: str, content: str) -> bool:
    payload = build_payload(content, secret)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_BYTES:
        print(f"消息请求体 {len(body)} 字节，超过飞书自定义机器人 20 KB 限制")
        return False

    req = urllib.request.Request(
        webhook, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        print(f"飞书推送失败：{type(e).__name__}（Webhook 已隐藏）")
        return False

    try:
        result = json.loads(raw)
    except ValueError:
        print(f"飞书返回非 JSON：{raw[:200]}")
        return False

    if not isinstance(result, dict):
        print("飞书返回的 JSON 不是对象")
        return False

    if response_succeeded(result):
        print(f"飞书推送成功（{len(body)} 字节）")
        return True
    print(f"飞书推送失败：code={result.get('code', result.get('StatusCode'))} "
          f"msg={result.get('msg', result.get('StatusMessage', ''))}")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="周报投递 · 飞书群自定义机器人")
    ap.add_argument("--report", default=None, help="指定 weekly-*.json（默认最新一期）")
    ap.add_argument("--latest", action="store_true", help="取 reports/ 下最新一期")
    ap.add_argument("--check", action="store_true", help="发送一条飞书通道自检消息")
    ap.add_argument("--dry-run", action="store_true", help="只打印消息，不发送")
    ap.add_argument("--card", action="store_true", help="发送本周变化卡片而非周报摘要")
    ap.add_argument("--preview", default=None, help="发送现有消息预览文件的内容")
    ap.add_argument("--prefix", default=None, help="覆盖消息标题")
    args = ap.parse_args()

    webhook = (os.environ.get("FEISHU_WEBHOOK") or "").strip()
    secret = (os.environ.get("FEISHU_SECRET") or "").strip()
    prefix = args.prefix or "微信小游戏周报"
    print(f"飞书机器人  {'已配置' if webhook else '未配置'}")

    if args.check:
        content = f"# {prefix} · 飞书通道自检\n收到此消息说明飞书 Webhook 可用。"
    else:
        report = Path(args.report) if args.report else report_sender.latest_report()
        if not report.is_absolute():
            report = (ROOT / report).resolve()
        data, md_path = report_sender.load_bundle(report)
        report_url = report_sender.github_blob_url(md_path or report)
        if args.card:
            content = report_sender.build_card_markdown(
                data, args.prefix or "", v2=True, preview=args.preview)
        else:
            content = report_sender.build_wecom_markdown(
                data, prefix, report_url, v2=True)
        print(f"发送周报：{report.name}（{'本周变化卡片' if args.card else '周报摘要'}）")

    if args.dry_run:
        print("-" * 60)
        print(content)
        print("-" * 60)
        print("（dry-run，未发送）")
        return EXIT_OK
    if not webhook or not secret:
        missing = "FEISHU_WEBHOOK" if not webhook else "FEISHU_SECRET"
        print(f"缺少 {missing}；请在仓库 Settings → Secrets and variables → Actions 配置。")
        return EXIT_BADCONFIG
    if not valid_webhook(webhook):
        print("FEISHU_WEBHOOK 必须是飞书 v2 群机器人地址（open.feishu.cn）。")
        return EXIT_BADCONFIG
    return EXIT_OK if post_message(webhook, secret, content) else EXIT_SENDFAIL


if __name__ == "__main__":
    sys.exit(main())

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
import re
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import send_wecom as report_sender

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MAX_BYTES = 20 * 1024
MAX_TABLES = 4
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
            "config": {"update_multi": True},
            "body": {
                "direction": "vertical",
                "elements": [{"tag": "markdown", "content": content}],
            },
        },
    }


def set_report_title(markdown: str, title: str) -> str:
    lines = markdown.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("# "):
            lines[i] = f"# {title}"
            return "\n".join(lines).replace("<br>", "；")
    return f"# {title}\n\n{markdown}".replace("<br>", "；")


def with_report_link(summary: str, report_url: str) -> str:
    if not report_url:
        return summary
    return f"{summary.rstrip()}\n\n[查看完整周报]({report_url})"


def standalone_report_url(data: dict, markdown_path: Path | None) -> str:
    site_url = str((data.get("meta") or {}).get("site_url") or "").strip().rstrip("/")
    if not site_url or markdown_path is None:
        return ""
    html_path = markdown_path.with_suffix(".html")
    if not html_path.is_file():
        return ""
    return f"{site_url}/reports/{html_path.name}"


def markdown_table_count(content: str) -> int:
    separator = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
    return sum(bool(separator.match(line)) for line in content.splitlines())


def prepare_payload(content: str, secret: str,
                    fallback_content: str | None = None,
                    timestamp: str | None = None) -> tuple[dict, bytes, bool]:
    payload = build_payload(content, secret, timestamp)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if (len(body) > MAX_BYTES or markdown_table_count(content) > MAX_TABLES) \
            and fallback_content is not None:
        payload = build_payload(fallback_content, secret, payload["timestamp"])
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return payload, body, True
    return payload, body, False


def response_succeeded(response: dict) -> bool:
    return (isinstance(response, dict)
            and response.get("code", response.get("StatusCode")) == 0)


def valid_webhook(webhook: str) -> bool:
    parsed = urlsplit(webhook)
    return (parsed.scheme == "https" and parsed.hostname == "open.feishu.cn"
            and parsed.path.startswith("/open-apis/bot/v2/hook/")
            and bool(parsed.path.removeprefix("/open-apis/bot/v2/hook/")))


def post_message(webhook: str, secret: str, content: str,
                 fallback_content: str | None = None) -> bool:
    payload, body, used_fallback = prepare_payload(content, secret, fallback_content)
    if used_fallback:
        print("完整周报超过飞书消息上限，改发精简版并附完整周报链接。")
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
    ap.add_argument("--full", action="store_true",
                    help="优先发送完整周报，超限时回退到精简版")
    ap.add_argument("--card", action="store_true", help="发送本周变化卡片而非周报摘要")
    ap.add_argument("--preview", default=None, help="发送现有消息预览文件的内容")
    ap.add_argument("--prefix", default=None, help="覆盖消息标题")
    args = ap.parse_args()

    webhook = (os.environ.get("FEISHU_WEBHOOK") or "").strip()
    secret = (os.environ.get("FEISHU_SECRET") or "").strip()
    prefix = args.prefix or "游戏应用市场周报"
    fallback_content = None
    print(f"飞书机器人  {'已配置' if webhook else '未配置'}")

    if args.check:
        content = f"# {prefix} · 飞书通道自检\n收到此消息说明飞书 Webhook 可用。"
    else:
        report = Path(args.report) if args.report else report_sender.latest_report()
        if not report.is_absolute():
            report = (ROOT / report).resolve()
        data, md_path = report_sender.load_bundle(report)
        report_url = standalone_report_url(data, md_path)
        if args.full:
            summary = report_sender.build_card_markdown(
                data, "", v2=True, preview=args.preview)
            fallback_content = with_report_link(summary, report_url)
            if md_path:
                content = set_report_title(md_path.read_text(encoding="utf-8"), prefix)
            else:
                print("::warning::找不到完整周报 Markdown，改发精简版。")
                content = fallback_content
        elif args.card:
            content = report_sender.build_card_markdown(
                data, args.prefix or "", v2=True, preview=args.preview)
        else:
            content = report_sender.build_wecom_markdown(
                data, prefix, report_url, v2=True)
        style = "完整周报（超限时回退精简版）" if args.full else (
            "本周变化卡片" if args.card else "周报摘要")
        print(f"发送周报：{report.name}（{style}）")

    if args.dry_run:
        _, body, used_fallback = prepare_payload(content, "dry-run", fallback_content)
        if used_fallback:
            content = fallback_content or content
            print("完整周报超限，dry-run 展示精简版回退内容。")
        print(f"消息请求体：{len(body)} / {MAX_BYTES} 字节")
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
    return EXIT_OK if post_message(
        webhook, secret, content, fallback_content) else EXIT_SENDFAIL


if __name__ == "__main__":
    sys.exit(main())

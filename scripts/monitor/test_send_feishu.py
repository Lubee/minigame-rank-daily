import unittest

from send_feishu import (
    MAX_BYTES,
    build_payload,
    make_sign,
    prepare_payload,
    response_succeeded,
    set_report_title,
    valid_webhook,
    with_report_link,
)


class FeishuPayloadTests(unittest.TestCase):
    def test_signature_matches_feishu_hmac_format(self):
        self.assertEqual(
            make_sign("1700000000", "example-secret"),
            "Gs3YRutIJpVN2FvEXKXHU6bEj2pVdSriQ0niig9EbUk=",
        )

    def test_payload_wraps_markdown_in_interactive_card_and_signs_it(self):
        payload = build_payload("# 周报\n本周变化", "example-secret", "1700000000")

        self.assertEqual(payload["msg_type"], "interactive")
        self.assertEqual(payload["timestamp"], "1700000000")
        self.assertEqual(payload["sign"], "Gs3YRutIJpVN2FvEXKXHU6bEj2pVdSriQ0niig9EbUk=")
        self.assertTrue(payload["card"]["config"]["update_multi"])
        self.assertEqual(
            payload["card"]["body"]["elements"][0],
            {"tag": "markdown", "content": "# 周报\n本周变化"},
        )

    def test_response_succeeds_only_for_zero_api_code(self):
        self.assertTrue(response_succeeded({"code": 0}))
        self.assertTrue(response_succeeded({"StatusCode": 0}))
        self.assertFalse(response_succeeded({"code": 19021, "msg": "sign failed"}))
        self.assertFalse(response_succeeded({"msg": "unexpected response"}))
        self.assertFalse(response_succeeded([]))

    def test_webhook_must_be_a_feishu_v2_bot_url(self):
        self.assertTrue(valid_webhook(
            "https://open.feishu.cn/open-apis/bot/v2/hook/example-token"))
        self.assertFalse(valid_webhook(
            "https://example.com/open-apis/bot/v2/hook/example-token"))
        self.assertFalse(valid_webhook(
            "http://open.feishu.cn/open-apis/bot/v2/hook/example-token"))

    def test_full_report_heading_uses_configured_title(self):
        report = set_report_title("# 微信小游戏周报\n\n玩法<br>详细说明", "游戏应用市场周报")
        self.assertEqual(report, "# 游戏应用市场周报\n\n玩法；详细说明")

    def test_oversized_full_report_falls_back_to_summary_with_full_report_link(self):
        link = "https://github.com/example/reports/weekly-2026-11-15.md"
        fallback = with_report_link("# 游戏应用市场周报\n摘要", link)
        payload, body, used_fallback = prepare_payload(
            "完整正文" * MAX_BYTES, "example-secret", fallback,
            timestamp="1700000000")

        self.assertTrue(used_fallback)
        self.assertLessEqual(len(body), MAX_BYTES)
        content = payload["card"]["body"]["elements"][0]["content"]
        self.assertIn("[查看完整周报](" + link + ")", content)
        self.assertNotIn("完整正文", content)

    def test_full_report_with_too_many_tables_falls_back_to_summary(self):
        full_report = "\n\n".join(
            f"| A{i} | B{i} |\n|---|---|\n| x | y |" for i in range(5))
        fallback = "# 游戏应用市场周报\n\n摘要\n\n[查看完整周报](https://example.com/report.md)"

        payload, _, used_fallback = prepare_payload(
            full_report, "example-secret", fallback,
            timestamp="1700000000")

        self.assertTrue(used_fallback)
        content = payload["card"]["body"]["elements"][0]["content"]
        self.assertEqual(content, fallback)


if __name__ == "__main__":
    unittest.main()

import unittest

from send_feishu import build_payload, make_sign, response_succeeded, valid_webhook


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


if __name__ == "__main__":
    unittest.main()

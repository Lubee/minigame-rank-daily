"""Separate WX tags must not shift publisher and ranking change fields."""
import unittest
from scrape_rank import parse_row


class GravityRowTests(unittest.TestCase):
    def test_wx_separate_tags_and_company(self):
        row = parse_row('Game\n休闲 · 1名\n益智\n海南公司\n3', 0, {
            'name': 'Game', 'tags': ['休闲 · 1名', '益智'], 'publisher': '海南公司', 'change': '3'})
        self.assertEqual((row['category'], row['category_rank'], row['subcategory']), ('休闲', 1, '益智'))
        self.assertEqual((row['publisher'], row['change'], row['slogan']), ('海南公司', '3', ''))

    def test_douyin_description_and_missing_optional_fields(self):
        row = parse_row('Game\nDescription\nCompany\n霸榜40天', 1, {
            'name': 'Game', 'description': '“Description”', 'publisher': 'Company', 'change': '霸榜40天'})
        self.assertEqual((row['category'], row['slogan'], row['publisher'], row['change']), ('', 'Description', 'Company', '霸榜40天'))
        blank = parse_row('Game\n--', 2, {'name': 'Game', 'publisher': '--'})
        self.assertEqual((blank['publisher'], blank['change']), ('', ''))
        with self.assertRaises(ValueError):
            parse_row('Game\nWrong Company', 0, {})

    def test_legacy_plain_text_parser_is_preserved(self):
        row = parse_row('NO.4\nGame\n休闲:2名 消除\nCompany\n1', 0)
        self.assertEqual((row['rank'], row['category'], row['publisher'], row['change']), (4, '休闲', 'Company', '1'))

    def test_more_tags_badge_is_not_a_publisher(self):
        row = parse_row('Game\n棋牌 · 1名\n牌类\n+2\nCompany\n1', 0, {
            'name': 'Game', 'tags': ['棋牌 · 1名', '牌类'], 'publisher': 'Company', 'change': '1'})
        self.assertEqual((row['subcategory'], row['publisher'], row['change']), ('牌类', 'Company', '1'))
        self.assertEqual(parse_row('Game', 0, {'name': 'Game', 'publisher': '+2'})['publisher'], '')

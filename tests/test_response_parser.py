import unittest

from app.grouping.response_parser import parse_json_object


class ResponseParserTests(unittest.TestCase):
    def test_parses_plain_object(self):
        self.assertEqual(parse_json_object('{"assignments": []}'), {"assignments": []})

    def test_parses_fenced_object(self):
        content = '```json\n{"assignments": []}\n```'
        self.assertEqual(parse_json_object(content), {"assignments": []})

    def test_removes_closed_thinking_block(self):
        content = '<think>reasoning with {braces}</think>\n{"assignments": []}'
        self.assertEqual(parse_json_object(content), {"assignments": []})

    def test_skips_invalid_earlier_brace(self):
        content = 'Use {this format}, then answer: {"assignments": []}'
        self.assertEqual(parse_json_object(content), {"assignments": []})

    def test_rejects_root_array(self):
        with self.assertRaises(ValueError):
            parse_json_object('[{"assignments": []}]')

    def test_rejects_unclosed_thinking_block(self):
        with self.assertRaises(ValueError):
            parse_json_object('<think>unfinished {"assignments": []}')


if __name__ == "__main__":
    unittest.main()

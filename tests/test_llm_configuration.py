import unittest
from unittest.mock import patch

from app.summarizer import initialize_llm


class InitializeLlmTests(unittest.TestCase):
    @patch("app.summarizer.ChatOpenAI")
    def test_omits_empty_reasoning_effort(self, chat_openai):
        initialize_llm("key", "https://example.test/v1", "model", reasoning_effort="")
        self.assertNotIn("reasoning_effort", chat_openai.call_args.kwargs)

    @patch("app.summarizer.ChatOpenAI")
    def test_forwards_lane_reasoning_effort(self, chat_openai):
        initialize_llm("key", "https://example.test/v1", "model", reasoning_effort="none")
        self.assertEqual(chat_openai.call_args.kwargs["reasoning_effort"], "none")


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database; no Brave query is ever sent)
from jobfinder.search import searching, session, shared
from jobfinder.web import tuning


class BraveSwitch(unittest.TestCase):
    def test_on_by_default_and_a_setting_turns_it_off(self):
        with patch.dict(shared.settings, {}, clear=False):
            shared.settings.pop("brave_search_enabled", None)
            self.assertTrue(searching.brave_enabled())
        with patch.dict(shared.settings, {"brave_search_enabled": False}):
            self.assertFalse(searching.brave_enabled())

    def test_switched_off_no_query_is_sent(self):
        with patch.dict(shared.settings, {"brave_search_enabled": False}), \
                patch.object(session, "web_search_ok", return_value=True), \
                patch.object(searching, "_search_brave") as asked:
            self.assertEqual(searching.search_web("web designer jobs"), [])
        asked.assert_not_called()

    def test_switched_on_the_query_goes_out(self):
        with patch.dict(shared.settings, {"brave_search_enabled": True}), \
                patch.object(session, "web_search_ok", return_value=True), \
                patch.object(searching, "_search_brave", return_value=[{"url": "https://a.example.com"}]) as asked:
            self.assertEqual(len(searching.search_web("web designer jobs")), 1)
        asked.assert_called_once()

    def test_the_tuning_page_has_the_switch(self):
        self.assertIn("brave_search_enabled", tuning.TUNING_SWITCHES)
        self.assertTrue(tuning.TUNING_SWITCHES["brave_search_enabled"][2])


if __name__ == "__main__":
    unittest.main()

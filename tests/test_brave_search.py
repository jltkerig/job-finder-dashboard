import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.search import searching, session, shared
from jobfinder.web.tuning import apply_tuning_form, TUNING_FIELDS


def reply(status, body=None):
    return MagicMock(status_code=status, json=MagicMock(return_value=body or {}), raise_for_status=MagicMock())


class BraveTests(unittest.TestCase):
    def setUp(self):
        for patcher in (patch.object(shared, "QUERY_DELAY", 0), patch.object(session, "engine_refused", False),
                        patch.object(session, "start_time", None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_results_are_url_title_content(self):
        body = {"web": {"results": [{"url": "https://a.com/job", "title": "Designer", "description": "Join us"}, {"title": "no url"}]}}
        with patch.object(searching, "_brave_get", return_value=reply(200, body)):
            self.assertEqual(searching.search_web("designer"),
                             [{"url": "https://a.com/job", "title": "Designer", "content": "Join us"}])

    def test_refusal_stops_the_web_search_for_this_run(self):
        with patch.object(searching, "_brave_get", return_value=reply(402)) as get, patch.object(searching.time, "sleep"):
            self.assertEqual(searching.search_web("designer"), [])
            self.assertEqual(searching.search_web("developer"), [])
        self.assertTrue(session.engine_refused)
        self.assertEqual(get.call_count, 1)

    def test_rate_limit_is_retried_once(self):
        with patch.object(searching, "_brave_get", side_effect=[reply(429), reply(200, {"web": {"results": []}})]) as get, \
                patch.object(searching.time, "sleep"):
            self.assertEqual(searching.search_web("designer"), [])
        self.assertEqual(get.call_count, 2)
        self.assertFalse(session.engine_refused)

    def test_tuning_saves_and_clears_the_key(self):
        form = {key: str(spec[5]) for key, spec in TUNING_FIELDS.items()}
        updated, error = apply_tuning_form(dict(form, brave_api_key="  abc  "), {})
        self.assertIsNone(error)
        self.assertEqual(updated["brave_api_key"], "abc")
        self.assertEqual(apply_tuning_form(dict(form, brave_api_key=""), {"brave_api_key": "abc"})[0]["brave_api_key"], "")
        self.assertEqual(apply_tuning_form(form, {"brave_api_key": "abc"})[0]["brave_api_key"], "abc")


if __name__ == "__main__":
    unittest.main()

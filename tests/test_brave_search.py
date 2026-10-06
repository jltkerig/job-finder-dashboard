import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.search import docker, searching, shared
from jobfinder.web.tuning import apply_tuning_form, TUNING_FIELDS


def reply(status, body=None):
    return MagicMock(status_code=status, json=MagicMock(return_value=body or {}), raise_for_status=MagicMock())


class BraveTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(shared, "QUERY_DELAY", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(setattr, docker, "brave_mode", False)
        docker.brave_mode = True

    def test_results_look_like_searxng(self):
        body = {"web": {"results": [{"url": "https://a.com/job", "title": "Designer", "description": "Join us"}, {"title": "no url"}]}}
        with patch.object(searching, "_brave_get", return_value=reply(200, body)), \
                patch.object(shared, "settings", {"brave_api_key": "k"}):
            self.assertEqual(searching.search_searxng("designer"),
                             [{"url": "https://a.com/job", "title": "Designer", "content": "Join us"}])

    def test_refusal_switches_to_searxng(self):
        with patch.object(searching, "_brave_get", return_value=reply(402)), \
                patch.object(searching.time, "sleep"), \
                patch.object(docker, "start_docker_desktop", return_value=True), \
                patch.object(docker, "start_searxng", return_value=True), \
                patch.object(searching, "_search_searxng", return_value=[{"url": "https://b.com"}]) as searx:
            self.assertEqual(searching.search_searxng("designer"), [{"url": "https://b.com"}])
        self.assertFalse(docker.brave_mode)
        searx.assert_called_once()

    def test_rate_limit_is_retried_once(self):
        with patch.object(searching, "_brave_get", side_effect=[reply(429), reply(200, {"web": {"results": []}})]) as get, \
                patch.object(searching.time, "sleep"):
            self.assertEqual(searching.search_searxng("designer"), [])
        self.assertEqual(get.call_count, 2)
        self.assertTrue(docker.brave_mode)

    def test_tuning_saves_and_clears_the_key(self):
        form = {key: str(spec[5]) for key, spec in TUNING_FIELDS.items()}
        updated, error = apply_tuning_form(dict(form, brave_api_key="  abc  "), {})
        self.assertIsNone(error)
        self.assertEqual(updated["brave_api_key"], "abc")
        self.assertEqual(apply_tuning_form(dict(form, brave_api_key=""), {"brave_api_key": "abc"})[0]["brave_api_key"], "")
        self.assertEqual(apply_tuning_form(form, {"brave_api_key": "abc"})[0]["brave_api_key"], "abc")


if __name__ == "__main__":
    unittest.main()

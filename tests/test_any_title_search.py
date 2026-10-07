import unittest
from unittest.mock import MagicMock, patch

from jobfinder.search.area_queries import area_queries
from jobfinder.sources.job_listings import ANY_TITLE, matching_title
from jobfinder.web import search_control
from jobfinder.web.core import app


class AnyTitleSearchTests(unittest.TestCase):
    def test_any_title_matches_real_jobs_only(self):
        self.assertTrue(matching_title("Barista", [ANY_TITLE]))
        self.assertTrue(matching_title("Registered Nurse", ["Web Designer", ANY_TITLE]))
        self.assertFalse(matching_title("", [ANY_TITLE]))
        self.assertFalse(matching_title("How to become a web designer", [ANY_TITLE]))
        self.assertFalse(matching_title("Barista", ["Web Designer"]))

    def test_empty_title_is_allowed_but_state_is_not(self):
        self.assertEqual(search_control.validate_search_criteria("", "MD")[:2], ("", "MD"))
        self.assertEqual(search_control.validate_search_criteria("", "")[3], "Enter a state.")

    def test_area_queries_use_places_not_titles(self):
        queries = area_queries("MD", ["Baltimore"], ["boards.greenhouse.io"])
        self.assertEqual(queries[0], 'site:boards.greenhouse.io "Baltimore"')
        self.assertIn('jobs "Baltimore" "MD"', queries)
        self.assertIn("jobs MD", queries)
        self.assertFalse(any("*" in query for query in queries))
        self.assertIn('site:boards.greenhouse.io "MD"', area_queries("MD", [], ["boards.greenhouse.io"]))

    def test_empty_title_search_keeps_the_saved_titles(self):
        process = MagicMock()
        process.poll.return_value = None
        with app.test_request_context(), \
                patch.object(search_control, "scraper_process", None), \
                patch.object(search_control.subprocess, "Popen", return_value=process) as popen, \
                patch.object(search_control.profile_store, "save_user_profile") as save_profile, \
                patch.object(search_control, "record_search_history"), \
                patch.object(search_control.webfiles, "SCRAPER_LOG_FILE", MagicMock()), \
                patch.object(search_control.webfiles, "STOP_REQUEST_FILE", MagicMock()):
            search_control.launch_search_process("", "MD", [])
        save_profile.assert_not_called()
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index("--job-title") + 1], "")


if __name__ == "__main__":
    unittest.main()

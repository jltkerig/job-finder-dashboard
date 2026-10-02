import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
import dashboard


class PrimaryJobTitle(unittest.TestCase):
    """Primary Job Title has its own box and is always searched, ahead of the other titles."""

    def save(self, primary, others):
        client = dashboard.app.test_client()
        with client.session_transaction() as session:
            session["csrf_token"] = "token"
        with patch.object(dashboard, "save_user_profile") as save:
            response = client.post("/save-profile", data={"csrf_token": "token", "primary_job_title": primary,
                                                          "job_titles": others})
        self.assertEqual(response.status_code, 302)
        args, kwargs = save.call_args
        return args[3], kwargs["primary_job_title"]

    def test_primary_leads_the_searched_titles(self):
        self.assertEqual(self.save("Web Designer", "Visual Designer, Web Producer"),
                         (["Web Designer", "Visual Designer", "Web Producer"], "Web Designer"))

    def test_primary_not_repeated_when_also_typed_below(self):
        self.assertEqual(self.save("Web Designer", "web designer, Web Producer"),
                         (["Web Designer", "Web Producer"], "Web Designer"))

    def test_no_primary_keeps_the_list(self):
        self.assertEqual(self.save("", "Visual Designer"), (["Visual Designer"], ""))

    def test_the_form_shows_primary_once(self):
        profile = {"first_name": "", "last_name": "", "state": "", "home_location": "", "avatar_data": "",
                   "primary_job_title": "Web Designer", "job_titles": ["Web Designer", "Web Producer"],
                   "cities": [], "skills": [], "work_history": [], "work_preferences": []}
        with dashboard.app.test_request_context("/dashboard"):
            html = dashboard.render_template("user-dashboard.html", profile=profile, companies=[], search_history=[],
                                             counts={"saved": 0}, skill_suggestions=[],
                                             filters={"title": "", "state": "", "status": "", "sort": "date_desc"})
        self.assertIn('id="primary-job-title" name="primary_job_title" type="text" data-title-suggest="single" value="Web Designer"', html)
        self.assertIn('id="job-titles" name="job_titles" type="text" data-title-suggest="list" value="Web Producer"', html)
        self.assertLess(html.index('for="primary-job-title"'), html.index('for="job-titles"'))


class TitleTypeAhead(unittest.TestCase):
    def test_matches_start_with_what_was_typed(self):
        from onet_data import title_matches
        matches = title_matches("web des")
        self.assertEqual(matches[0], "Web Designer")
        self.assertTrue(all("web" in m.lower() for m in matches))

    def test_plural_occupation_names_are_dropped(self):
        from onet_data import title_matches
        matches = title_matches("graphic designer")
        self.assertIn("Graphic Designer", matches)
        self.assertNotIn("Graphic Designers", matches)

    def test_too_short_or_unknown_gives_nothing(self):
        from onet_data import title_matches
        self.assertEqual(title_matches("w"), [])
        self.assertEqual(title_matches("xyzq"), [])

    def test_endpoint(self):
        data = dashboard.app.test_client().get("/job-title-matches?q=ux%20des").get_json()
        self.assertTrue(data["matches"][0].lower().startswith("ux designer"))


if __name__ == "__main__":
    unittest.main()

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
import dashboard
from jobfinder.web import easy_apply


class EasyApplyFlag(unittest.TestCase):
    def test_it_is_read_from_the_jobs_listing_details(self):
        self.assertTrue(easy_apply.is_easy_apply(json.dumps({"easy_apply": True, "location": "Dallas, TX"})))
        self.assertTrue(easy_apply.is_easy_apply({"easy_apply": True}))
        self.assertFalse(easy_apply.is_easy_apply(json.dumps({"location": "Dallas, TX"})))
        self.assertFalse(easy_apply.is_easy_apply(""))
        self.assertFalse(easy_apply.is_easy_apply("not json"))
        self.assertFalse(easy_apply.is_easy_apply(None))

    def test_saved_jobs_get_the_flag(self):
        jobs = easy_apply.add_to([{"listing_details": json.dumps({"easy_apply": True})}, {"listing_details": "{}"}, {}])
        self.assertEqual([job["easy_apply"] for job in jobs], [True, False, False])


class EasyApplyBadge(unittest.TestCase):
    def test_the_badge_has_linkedins_in_square_and_its_name(self):
        macro = dashboard.app.jinja_env.get_template("_easy_apply.html").module.easy_apply_badge
        html = str(macro())
        self.assertIn('class="easy-apply-badge"', html)
        self.assertIn("<svg", html)
        self.assertIn("Easy Apply", html)

    def test_the_results_and_the_dashboard_show_it_only_for_easy_apply_jobs(self):
        for name, marker in (("index.html", "company.details.easy_apply"), ("user-dashboard.html", "company.easy_apply")):
            source = (Path(__file__).resolve().parents[1] / "templates" / name).read_text(encoding="utf-8")
            self.assertIn("{% if " + marker + " %}", source)
            self.assertIn('{% from "_easy_apply.html" import easy_apply_badge %}', source)
        dash = (Path(__file__).resolve().parents[1] / "templates" / "user-dashboard.html").read_text(encoding="utf-8")
        self.assertIn("{% if job.easy_apply %}", dash)  # the Apply Queue too


if __name__ == "__main__":
    unittest.main()

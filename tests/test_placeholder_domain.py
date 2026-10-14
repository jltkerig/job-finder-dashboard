import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.sources.employer_jobs import Employer
from jobfinder.web.listing_queries import fix_placeholder_domains


class EmployerDomain(unittest.TestCase):
    def test_a_board_set_up_by_slug_is_saved_under_the_host_its_posting_is_on(self):
        employer = Employer({"name": "Acme", "system": "ashby", "slug": "acme"})
        self.assertEqual(employer.domain, "")
        self.assertEqual(employer.domain_for("https://jobs.ashbyhq.com/acme/1234"), "jobs.ashbyhq.com")
        self.assertEqual(employer.domain_for("https://www.example-jobs.com/p/1"), "example-jobs.com")

    def test_a_named_company_domain_wins(self):
        employer = Employer({"name": "Acme", "system": "ashby", "slug": "acme", "domain": "acme.com"})
        self.assertEqual(employer.domain_for("https://jobs.ashbyhq.com/acme/1234"), "acme.com")

    def test_a_host_alone_gives_the_domain(self):
        employer = Employer({"name": "Acme", "system": "workday", "host": "acme.wd1.myworkdayjobs.com",
                             "tenant": "acme", "site": "External"})
        self.assertEqual(employer.domain, "acme.wd1.myworkdayjobs.com")


class PlaceholderRows(unittest.TestCase):
    def test_saved_placeholder_rows_show_the_real_host(self):
        rows = [{"domain": "boards.example", "career_url": "https://job-boards.greenhouse.io/acme/jobs/1", "source_url": ""},
                {"domain": "acme.com", "career_url": "https://acme.com/jobs/1", "source_url": ""},
                {"domain": "Boards.Example", "career_url": "", "source_url": "https://www.lever.co/acme/1"}]
        fixed = fix_placeholder_domains(rows)
        self.assertEqual([row["domain"] for row in fixed], ["job-boards.greenhouse.io", "acme.com", "lever.co"])


if __name__ == "__main__":
    unittest.main()

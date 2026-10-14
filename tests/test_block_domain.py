import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
import dashboard


def block(domain, source_type="LinkedIn"):
    """(status code, whether the domain was added to the blocklist) for Block Domain on a job with this domain."""
    client = dashboard.app.test_client()
    with client.session_transaction() as session:
        session["csrf_token"] = "t"
    cursor = MagicMock()
    cursor.fetchone.return_value = {"domain": domain, "source_type": source_type}
    connection = MagicMock()
    connection.cursor.return_value = cursor
    with patch("jobfinder.web.listing_actions.db.connect", return_value=connection), \
            patch("jobfinder.web.listing_actions.schema.ensure_job_tracking_columns"), \
            patch("jobfinder.web.listing_actions.add_domain_to_blocklist") as add:
        response = client.post("/block-domain/5", headers={"X-CSRF-Token": "t"})
    return response.status_code, add.called


class BlockDomainTests(unittest.TestCase):
    def test_a_job_sites_own_domain_is_never_blocked(self):
        self.assertEqual(block("linkedin.com"), (400, False))
        self.assertEqual(block("https://www.indeed.com/"), (400, False))

    def test_an_employers_domain_is_blocked(self):
        self.assertEqual(block("mach7t.com"), (200, True))


if __name__ == "__main__":
    unittest.main()

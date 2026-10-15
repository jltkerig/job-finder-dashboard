import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
import dashboard
from jobfinder import paths
from jobfinder.web import companies_routes  # noqa: F401  (registers the routes)


class PageState(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patcher = mock.patch.object(paths, "COMPANIES_PAGE_FILE", Path(folder.name) / "companies_page.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = dashboard.app.test_client()
        with self.client.session_transaction() as session:
            session["csrf_token"] = "token"
        self.headers = {"X-CSRF-Token": "token"}

    def test_results_and_hidden_list_are_kept_and_read_back(self):
        self.assertEqual(self.client.get("/companies/page-state").get_json().get("results"), None)
        results = {"place": "Bel Air, MD", "shown": "Bel Air, MD", "miles": "15", "companies": [{"id": "node/1", "name": "Acme"}]}
        self.client.post("/companies/page-state", json={"results": results}, headers=self.headers)
        self.client.post("/companies/page-state", json={"hidden": ["acme|1 main st"]}, headers=self.headers)  # a second save keeps the first
        state = self.client.get("/companies/page-state").get_json()
        self.assertEqual(state["results"], results)
        self.assertEqual(state["hidden"], ["acme|1 main st"])

    def test_junk_is_ignored(self):
        self.client.post("/companies/page-state", json={"results": "nope", "hidden": "nope"}, headers=self.headers)
        self.assertEqual(self.client.get("/companies/page-state").get_json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
import dashboard


class ExtensionUpdates(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dist = Path(folder.name)
        for name in ("web-job-scraper-v0.1.2.xpi", "web-job-scraper-v0.1.10.xpi", "notes.txt"):
            (self.dist / name).write_bytes(name.encode())
        patcher = patch.object(dashboard, "extension_dist_dir", return_value=self.dist)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = dashboard.app.test_client()

    def test_firefox_is_offered_the_newest_build(self):
        data = self.client.get("/extension/updates.json").get_json()
        [update] = data["addons"]["web-job-scraper@jamie.local"]["updates"]
        self.assertEqual(update["version"], "0.1.10")  # 10 > 2, not text order
        self.assertEqual(update["update_link"], "http://localhost/extension/web-job-scraper-v0.1.10.xpi")
        digest = hashlib.sha256(b"web-job-scraper-v0.1.10.xpi").hexdigest()
        self.assertEqual(update["update_hash"], f"sha256:{digest}")

    def test_the_build_can_be_downloaded_and_nothing_else(self):
        response = self.client.get("/extension/web-job-scraper-v0.1.2.xpi")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/x-xpinstall")
        response.close()
        self.assertEqual(self.client.get("/extension/notes.txt").status_code, 404)
        self.assertEqual(self.client.get("/extension/..%2Fdashboard.py").status_code, 404)

    def test_no_builds_means_no_updates(self):
        for path in self.dist.iterdir():
            path.unlink()
        data = self.client.get("/extension/updates.json").get_json()
        self.assertEqual(data["addons"]["web-job-scraper@jamie.local"]["updates"], [])


if __name__ == "__main__":
    unittest.main()

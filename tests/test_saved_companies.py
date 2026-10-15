import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jobfinder import paths
from jobfinder.web import saved_companies


class SavedCompaniesTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(paths, "SAVED_COMPANIES_FILE", Path(self.folder.name) / "saved_companies.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.folder.cleanup)

    def test_save_keeps_what_the_search_found_and_does_not_duplicate(self):
        entry, added = saved_companies.add({"name": "Acme Widgets, Inc.", "website": "acme.com", "address": "1 Main St, Bel Air, MD 21014",
                                            "phone": "410-555-0100", "notes": "Found near Bel Air"})
        self.assertTrue(added)
        self.assertEqual(entry["website"], "https://acme.com")
        again, added_again = saved_companies.add({"name": "ACME Widgets Inc", "address": "elsewhere"})
        self.assertFalse(added_again)
        self.assertEqual(again["id"], entry["id"])
        self.assertEqual([c["name"] for c in saved_companies.load()], ["Acme Widgets, Inc."])

    def test_listed_nearest_first_and_removed(self):
        near, _ = saved_companies.add({"name": "Near Co", "address": "Bel Air, MD 21014"})
        far, _ = saved_companies.add({"name": "Far Co", "address": "Baltimore, MD 21201"})
        listed = saved_companies.everything((39.5359, -76.3483))
        self.assertEqual([c["name"] for c in listed][:2], ["Near Co", "Far Co"])
        self.assertLess(listed[0]["miles"], listed[1]["miles"])
        saved_companies.delete(near["id"])
        self.assertEqual([c["name"] for c in saved_companies.load()], ["Far Co"])
        with self.assertRaises(KeyError):
            saved_companies.delete("nope")


if __name__ == "__main__":
    unittest.main()

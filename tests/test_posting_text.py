import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.sources import posting_text as pt


class PostingTextTests(unittest.TestCase):
    def test_workday_page_maps_to_its_data_service(self):
        http = MagicMock()
        http.get.return_value = MagicMock(status_code=200, json=lambda: {"jobPostingInfo": {"jobDescription": "<p>Design</p>"}})
        url = "https://acme.wd5.myworkdayjobs.com/en-US/External/job/Remote/Designer_R1"
        self.assertEqual(pt._workday(url, http), "<p>Design</p>")
        http.get.assert_called_once_with("https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External/job/Remote/Designer_R1")

    def test_oracle_page_maps_to_its_data_service(self):
        http = MagicMock()
        http.get.return_value = MagicMock(status_code=200, json=lambda: {"items": [{"ExternalDescriptionStr": "About", "ExternalQualificationsStr": "Figma"}]})
        text = pt._oracle("https://x.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/42", http)
        self.assertEqual(text, "About\n\nFigma")
        self.assertIn('ById;Id="42",siteNumber=CX_1', http.get.call_args[1]["params"]["finder"])

    def test_other_sites_and_failures_give_nothing(self):
        self.assertEqual(pt.posting_text("https://acme.com/jobs/1"), "")
        with patch.object(pt, "_workday", side_effect=ValueError):
            self.assertEqual(pt.posting_text("https://acme.wd1.myworkdayjobs.com/x/job/y"), "")

    def test_workday_remote_type_is_trusted_over_page_data(self):
        with patch.object(pt, "_workday_info", return_value={"remoteType": "Office"}):
            self.assertEqual(pt.workday_remote_type("https://acme.wd5.myworkdayjobs.com/Ext/job/X/Designer_R1"), "Office")
        self.assertIsNone(pt.workday_remote_type("https://acme.com/jobs/1"))


if __name__ == "__main__":
    unittest.main()

import json
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.web import apply_stats


def job(applied_on=None):
    return {"listing_details": json.dumps({"applied_on": applied_on} if applied_on else {})}


class AppliedByMonthTests(unittest.TestCase):
    def test_recent_months_run_back_across_the_year(self):
        months = apply_stats.recent_months(count=4, today=date(2026, 2, 10))
        self.assertEqual([key for key, _ in months], ["2026-02", "2026-01", "2025-12", "2025-11"])
        self.assertEqual(months[0][1], "Feb 2026")

    def test_jobs_are_counted_in_the_month_they_were_applied_to(self):
        rows = [job("2026-10-02"), job("2026-10-14"), job("2026-09-30")]
        result = apply_stats.by_month(rows, today=date(2026, 10, 14))
        counts = {month["key"]: month["count"] for month in result["months"]}
        self.assertEqual(counts["2026-10"], 2)
        self.assertEqual(counts["2026-09"], 1)
        self.assertEqual(counts["2026-08"], 0)
        self.assertEqual(result["months"][0]["key"], "2026-10")

    def test_older_and_undated_jobs_are_counted_apart(self):
        rows = [job("2025-01-05"), job(), job("not a date")]
        result = apply_stats.by_month(rows, today=date(2026, 10, 14))
        self.assertEqual(result["earlier"], 1)
        self.assertEqual(result["undated"], 2)
        self.assertEqual(sum(month["count"] for month in result["months"]), 0)

    def test_without_a_database_every_month_is_zero(self):
        result = apply_stats.get_applied_by_month()
        self.assertEqual(len(result["months"]), apply_stats.MONTHS_SHOWN)
        self.assertEqual((result["earlier"], result["undated"]), (0, 0))


if __name__ == "__main__":
    unittest.main()

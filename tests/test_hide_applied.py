import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.web.listing_queries import hide_applied, hide_queued, hide_saved, merge_duplicates


def job(job_id, name, title, status="None", kept=0, source="Web"):
    return {"id": job_id, "name": name, "career_job_title": title, "application_status": status,
            "is_kept": kept, "source_type": source, "career_credibility": 5, "date_found": "2026-10-01"}


class HideAppliedTests(unittest.TestCase):
    def test_applied_jobs_leave_the_search_results(self):
        rows = [job(1, "Acme", "Web Designer", "Applied", kept=1), job(2, "Beta", "UI Designer"),
                job(3, "Gamma", "Art Director", "Interview", kept=1), job(4, "Delta", "Illustrator", "Saved", kept=1)]
        shown = hide_applied(merge_duplicates(rows), rows)
        self.assertEqual([row["id"] for row in shown], [2, 4])

    def test_a_copy_of_an_applied_job_on_another_board_leaves_too(self):
        applied = job(1, "Acme", "Web Designer", "Applied")
        copy = job(2, "Acme", "Web Designer", source="Indeed")
        saved_copy = job(3, "Acme", "Web Designer", kept=1, source="LinkedIn")  # outranks the applied one when merged
        other = job(4, "Beta", "UI Designer")
        rows = [applied, copy, saved_copy, other]
        shown = hide_applied(merge_duplicates(rows), rows)
        self.assertEqual([row["id"] for row in shown], [4])

    def test_nothing_applied_changes_nothing(self):
        rows = [job(1, "Acme", "Web Designer"), job(2, "Beta", "UI Designer", "Saved", kept=1)]
        self.assertEqual(hide_applied(rows, rows), rows)


class HideQueuedTests(unittest.TestCase):
    def test_jobs_added_to_the_apply_queue_leave_the_search_results(self):
        queued = dict(job(1, "Acme", "Web Designer"), details={"apply_queued": True})
        other = dict(job(2, "Beta", "UI Designer"), details={})
        not_queued_now = dict(job(3, "Gamma", "Art Director"), details={"apply_queued_at": "2026-10-01T10:00:00"})
        rows = [queued, other, not_queued_now]
        self.assertEqual([r["id"] for r in hide_queued(rows, rows)], [2, 3])

    def test_a_copy_of_a_queued_job_on_another_board_leaves_too(self):
        queued = dict(job(1, "Acme", "Web Designer"), details={"apply_queued": True})
        copy = dict(job(2, "Acme", "Web Designer", source="Indeed"), details={})
        other = dict(job(3, "Beta", "UI Designer"), details={})
        everything = [queued, copy, other]
        shown = hide_queued(merge_duplicates(everything), everything)
        self.assertEqual([r["id"] for r in shown], [3])

    def test_rows_without_details_are_left_alone(self):
        rows = [job(1, "Acme", "Web Designer"), job(2, "Beta", "UI Designer")]
        self.assertEqual(hide_queued(rows, rows), rows)


class HideSavedTests(unittest.TestCase):
    def test_saved_jobs_and_their_copies_leave_the_search_results(self):
        saved = job(1, "Acme", "Web Designer", kept=1)
        copy = job(2, "Acme", "Web Designer", source="Indeed")
        other = job(3, "Beta", "UI Designer")
        rows = [saved, copy, other]
        self.assertEqual([r["id"] for r in hide_saved(rows, rows)], [3])
        self.assertEqual([r["id"] for r in hide_saved([copy, other], rows)], [3])


if __name__ == "__main__":
    unittest.main()

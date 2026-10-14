import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobfinder.web import resume_builder_process as rb


class ResumeBuilderProcessTests(unittest.TestCase):
    def setUp(self):
        rb._started = None
        self.folder = Path(tempfile.mkdtemp())

    def test_nothing_happens_without_the_resume_builder_folder(self):
        with patch.object(rb.subprocess, "Popen") as popen:
            self.assertFalse(rb.start_if_needed(self.folder, 59999))
        popen.assert_not_called()

    def test_it_is_started_when_nothing_answers_and_not_twice(self):
        (self.folder / "app.py").write_text("print('hi')", encoding="utf-8")
        process = MagicMock()
        process.poll.return_value = None  # still running
        with patch.object(rb, "is_running", return_value=False), \
                patch.object(rb.subprocess, "Popen", return_value=process) as popen:
            self.assertTrue(rb.start_if_needed(self.folder, 59999))
            self.assertFalse(rb.start_if_needed(self.folder, 59999))  # on its way: not started a second time
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(popen.call_args[1]["cwd"], str(self.folder))

    def test_it_is_started_again_after_it_stops(self):
        (self.folder / "app.py").write_text("print('hi')", encoding="utf-8")
        process = MagicMock()
        process.poll.return_value = 1  # exited
        with patch.object(rb, "is_running", return_value=False), \
                patch.object(rb.subprocess, "Popen", return_value=process) as popen:
            rb.start_if_needed(self.folder, 59999)
            rb.start_if_needed(self.folder, 59999)
        self.assertEqual(popen.call_count, 2)

    def test_a_copy_that_is_already_answering_is_left_alone(self):
        (self.folder / "app.py").write_text("print('hi')", encoding="utf-8")
        with patch.object(rb, "is_running", return_value=True), patch.object(rb.subprocess, "Popen") as popen:
            self.assertFalse(rb.start_if_needed(self.folder, 59999))
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()

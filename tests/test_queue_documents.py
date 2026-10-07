import json
import unittest
from unittest.mock import patch

from jobfinder.web import queue_documents


QUEUE = [{"id": 156, "name": "Owner", "career_job_title": "Senior Product Designer"},
         {"id": 158, "name": "Cengage Group", "career_job_title": "Principal UX Designer"}]
FILES = {158: [{"kind": "resume"}, {"kind": "cover_letter"}]}


class QueueDocumentsTests(unittest.TestCase):
    def setUp(self):
        queue_documents._running.clear()
        queue_documents._tries.clear()
        self.addCleanup(setattr, queue_documents, "enabled", False)

    def test_lists_only_jobs_missing_files(self):
        with patch.object(queue_documents, "files_by_job", return_value=FILES):
            missing = queue_documents.missing_documents(QUEUE)
        self.assertEqual(missing, [{"job_id": 156, "job_title": "Senior Product Designer", "company": "Owner",
                                    "needs": ["resume", "cover_letter"]}])

    def test_never_starts_claude_unless_the_dashboard_switched_it_on(self):
        with patch.object(queue_documents.threading, "Thread") as thread:
            self.assertFalse(queue_documents.start_if_needed())
        thread.assert_not_called()

    def test_starts_once_for_missing_jobs_and_limits_tries(self):
        queue_documents.start()
        with patch("jobfinder.web.auto_apply.get_apply_queue", return_value=QUEUE), \
                patch.object(queue_documents, "files_by_job", return_value=FILES), \
                patch.object(queue_documents.threading, "Thread") as thread:
            self.assertTrue(queue_documents.start_if_needed())
            self.assertFalse(queue_documents.start_if_needed())  # already writing
            self.assertEqual(queue_documents.writing_ids(), {156})
            queue_documents._running.clear()
            self.assertTrue(queue_documents.start_if_needed())
            queue_documents._running.clear()
            self.assertFalse(queue_documents.start_if_needed())  # two tries a day
        jobs = thread.call_args.kwargs["args"][0]
        self.assertEqual([job["job_id"] for job in jobs], [156])

    def test_command_gives_claude_only_the_resume_builder_tools(self):
        command = queue_documents._command([{"job_id": 156, "needs": ["resume"]}])
        self.assertIn("-p", command)
        self.assertEqual(command[command.index("--allowedTools") + 1], "mcp__resume-builder")
        self.assertIn("--strict-mcp-config", command)
        config = json.loads(command[command.index("--mcp-config") + 1])
        self.assertTrue(config["mcpServers"]["resume-builder"]["args"][0].endswith("mcp_server.py"))


if __name__ == "__main__":
    unittest.main()

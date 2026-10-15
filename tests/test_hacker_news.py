import unittest
from unittest import mock

from jobfinder.sources import hacker_news, job_feeds

TITLES = ["Frontend Developer", "Graphic Designer"]


def comment(identifier, html):
    return {"id": str(identifier), "text": html, "created_at": "2026-10-02T14:00:00Z"}


COMMENTS = [
    comment(1, "Acme (https://acme.com) | Senior Frontend Developer | REMOTE (US) | Full-time<p>We build things. Apply at <a href=\"https://acme.com/jobs/1\">acme.com/jobs/1</a>"),
    comment(2, "Beta Studio | Graphic Designer / Motion Designer | REMOTE | $90k"),
    comment(3, "Gamma | Backend Engineer, Data Engineer | REMOTE (US) | Full-time"),
    comment(4, "Delta | Frontend Developer | REMOTE (EU only) | Full-time"),
    comment(5, "Epsilon | Frontend Developer | San Francisco, CA | ONSITE"),
    comment(6, "Zeta is hiring a frontend developer, everything is great, contact us anywhere."),
    comment(7, "Eta | Hiring: Product Designer, Graphic Designer | Remote (anywhere) | Full-time"),
]


class HackerNewsFeed(unittest.TestCase):
    def found(self, usa_only=True):
        return {job["name"]: job for job in hacker_news.matching_jobs(COMMENTS, TITLES, usa_only)}

    def test_remote_us_openings_with_a_matching_title_are_kept(self):
        jobs = self.found()
        self.assertEqual(set(jobs), {"Acme", "Beta Studio", "Eta"})
        self.assertEqual(jobs["Acme"]["title"], "Senior Frontend Developer")
        self.assertEqual(jobs["Acme"]["url"], "https://news.ycombinator.com/item?id=1")
        self.assertEqual(jobs["Acme"]["usa_score"], 6)
        self.assertEqual(jobs["Beta Studio"]["title"], "Graphic Designer")
        self.assertEqual(jobs["Eta"]["title"], "Graphic Designer")  # from the "Hiring:" part, the matching role

    def test_other_regions_onsite_and_free_form_comments_are_left_out(self):
        jobs = self.found()
        for name in ("Gamma", "Delta", "Epsilon", "Zeta"):
            self.assertNotIn(name, jobs)

    def test_the_feed_is_registered_and_links_on_hacker_news(self):
        feed = next(feed for feed in job_feeds.FEEDS if feed.name == "Hacker News")
        self.assertIn("news.ycombinator.com", feed.hosts)
        self.assertIn("Hacker News", job_feeds.FEED_NAMES)

    def test_the_newest_thread_is_found_and_only_openings_are_read(self):
        def fake_get(url, params=None, headers=None, timeout=None):
            class Response:
                def raise_for_status(self):
                    pass

                def json(self):
                    if url.endswith("/search_by_date"):
                        return {"hits": [{"objectID": "9", "title": "Ask HN: Who wants to be hired? (October 2026)"},
                                         {"objectID": "7", "title": "Ask HN: Who is hiring? (October 2026)"}]}
                    return {"nbPages": 1, "hits": [{"objectID": "70", "parent_id": 7, "comment_text": "Acme | Frontend Developer | REMOTE (US)"},
                                                   {"objectID": "71", "parent_id": 70, "comment_text": "a reply"}]}
            return Response()
        with mock.patch.object(hacker_news.requests, "get", fake_get):
            found = hacker_news._comments(5)
        self.assertEqual([c["id"] for c in found], ["70"])


if __name__ == "__main__":
    unittest.main()

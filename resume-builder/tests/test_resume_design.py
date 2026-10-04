"""Résumé Design: analysing a résumé, the font list and look-alikes, the portfolio reader and the design form.
Made-up data only; every folder is temporary and nothing uses the network."""
import json
import os
import re
import sys
import tempfile
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest import mock

TEMP = Path(tempfile.mkdtemp(prefix="resume-builder-test-"))
os.environ.setdefault("RESUME_OUTPUT_DIR", str(TEMP / "user-builds"))
os.environ.setdefault("RESUME_DATA_DIR", str(TEMP / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as web  # noqa: E402
import builds  # noqa: E402
import config  # noqa: E402
import design  # noqa: E402
import design_report  # noqa: E402
import jobfinder_db  # noqa: E402
import pdf_render  # noqa: E402
import resume_file  # noqa: E402
from models import DEFAULT_LAYOUT, ResumeContent, merge_layout  # noqa: E402

assert "resume-builder-test-" in str(config.DATA_DIR), "tests must never use the real data folder"
design.DOWNLOADS_ENABLED = False

CONTENT = ResumeContent.model_validate({
    "full_name": "Jane Doe", "headline": "Web Developer", "contact": ["jane@example.com", "555-0100", "Springfield, MD"],
    "sections": [
        {"title": "Summary", "text": "Web developer who builds accessible sites. " * 4},
        {"title": "Experience", "items": [{"heading": "Web Developer", "subheading": "Example Co", "location": "Springfield, MD",
                                           "dates": "2020 - 2024", "bullets": ["Built pages in HTML and CSS", "Improved accessibility"]}]},
        {"title": "Skills", "text": "HTML, CSS, JavaScript"}]})
LAYOUT = merge_layout({"font_family": "Nonexistent Sans", "margin_left": 0.9, "margin_right": 0.8, "margin_top": 0.6,
                       "bullet_char": "–", "contact_separator": "•", "line_spacing": 1.4, "heading_rule": True})


def pdf_bytes(layout=LAYOUT):
    out = BytesIO()
    pdf_render.render_resume(CONTENT, layout, out)
    return out.getvalue()


def pdf_file(layout=LAYOUT):
    path = TEMP / "sample.pdf"
    path.write_bytes(pdf_bytes(layout))
    return path


class Analysis(unittest.TestCase):
    def test_what_the_page_shows_is_read_back(self):
        facts = design_report.analyze_pdf(pdf_file())
        self.assertEqual(facts["page"], "Letter")
        self.assertEqual(facts["columns"], 1)
        self.assertAlmostEqual(facts["margins"]["left"], 0.9, delta=0.04)
        self.assertAlmostEqual(facts["margins"]["right"], 0.8, delta=0.25)  # the right edge is the longest line, not the margin
        self.assertAlmostEqual(facts["margins"]["top"], 0.6, delta=0.12)
        self.assertEqual(facts["name"]["text"], "Jane Doe")
        self.assertEqual(facts["header"]["separator"], "•")
        self.assertEqual(facts["bullets"]["char"], "–")
        self.assertEqual(facts["headings"]["case"], "upper")
        self.assertEqual(facts["headings"]["items"], ["SUMMARY", "EXPERIENCE", "SKILLS"])
        self.assertTrue(facts["headings"]["rule"])
        self.assertAlmostEqual(facts["body"]["spacing"], 1.4, delta=0.1)
        self.assertGreaterEqual(facts["dates"]["right"], 1)

    def test_a4_paper_is_recognised(self):
        self.assertEqual(design_report.analyze_pdf(pdf_file(merge_layout(LAYOUT, {"page_size": "a4"})))["page"], "A4")

    def test_the_description_names_every_part(self):
        facts = design_report.analyze_pdf(pdf_file())
        rows = dict(design_report.describe(facts, LAYOUT))
        for label in ("Page", "Margins", "Name", "Header and Contact", "Section Headings", "Section Order", "Body Text",
                      "Spacing", "Bullets", "Dates", "Fonts Used"):
            self.assertIn(label, rows)
        self.assertIn("Left 0.9 in", rows["Margins"])
        self.assertIn("SUMMARY → EXPERIENCE → SKILLS", rows["Section Order"])

    def test_the_numbers_become_settings_within_their_limits(self):
        settings = design_report.layout_from_facts(design_report.analyze_pdf(pdf_file()), LAYOUT)
        merge_layout(settings)  # every value is valid
        self.assertEqual(settings["bullet_char"], "–")
        self.assertEqual(settings["contact_separator"], "•")
        self.assertAlmostEqual(settings["margin_left"], 0.9, delta=0.04)

    def test_without_page_facts_it_describes_the_settings(self):
        rows = dict(design_report.describe(None, merge_layout({"font_family": "Georgia"})))
        self.assertIn("Georgia", rows["Name"])

    def test_zero_width_spaces_and_blank_lines_are_ignored(self):
        self.assertEqual(design_report.analyze_pdf(pdf_file())["name"]["text"], "Jane Doe")
        self.assertNotIn("​", "".join(design_report.analyze_pdf(pdf_file())["headings"]["items"]))


class Fonts(unittest.TestCase):
    def test_google_fonts_are_on_the_list(self):
        _, google = design.font_choices()
        for name in ("Unna", "Roboto Condensed", "Fira Sans Extra Condensed", "Merriweather", "Courier Prime", "Caveat"):
            self.assertIn(name, google)

    def test_status_of_a_google_an_unknown_and_an_empty_font(self):
        self.assertEqual(design.font_status("Merriweather")["status"], "google")
        self.assertEqual(design.font_status("")["status"], "same")
        gotham = design.font_status("Gotham")
        self.assertEqual(gotham["status"], "missing")
        self.assertEqual(gotham["similar"][0], "Montserrat")

    def test_installed_fonts_say_so(self):
        installed = design.installed_fonts()
        if not installed:
            self.skipTest("no Windows fonts here")
        self.assertEqual(design.font_status(installed[0])["status"], "installed")

    def test_look_alikes_for_unknown_fonts_follow_their_kind(self):
        self.assertTrue(all(design.GOOGLE_FONTS.get(f) == "mono" for f in design.similar_fonts("Zorg Mono")))
        self.assertTrue(set(design.similar_fonts("Zorg Serif")) <= {n for n, k in design.GOOGLE_FONTS.items() if k == "serif"})
        self.assertIn("Arimo", design.similar_fonts("Helvetica Neue Light") + design.similar_fonts("Helvetica"))
        self.assertTrue(design.similar_fonts("Totally Unknown"))

    def test_font_kind(self):
        self.assertEqual(design.font_kind("Merriweather"), "serif")
        self.assertEqual(design.font_kind("Roboto Condensed"), "sans")
        self.assertEqual(design.font_kind("Zorg Serif"), "serif")
        self.assertEqual(design.font_kind("Zorg Sans Serif"), "sans")

    def test_nothing_is_downloaded_when_downloads_are_off(self):
        self.assertIsNone(design.download_google("Raleway"))

    def test_a_google_font_is_downloaded_once_and_cached(self):
        css = ("@font-face { font-family: 'Unna'; font-style: normal; font-weight: 400; src: url(https://fonts.gstatic.com/s/unna/r.ttf) format('truetype'); }\n"
               "@font-face { font-family: 'Unna'; font-style: normal; font-weight: 700; src: url(https://fonts.gstatic.com/s/unna/b.ttf) format('truetype'); }\n"
               "@font-face { font-family: 'Unna'; font-style: italic; font-weight: 400; src: url(https://evil.example/i.ttf) format('truetype'); }")
        calls = []

        def fake(url, limit=0):
            calls.append(url)
            return css.encode() if "googleapis" in url else b"\x00\x01\x00\x00fakefont"
        with mock.patch.object(design, "DOWNLOADS_ENABLED", True), mock.patch.object(design, "_fetch", fake):
            files = design.download_google("Unna")
            again = design.download_google("Unna")
        self.assertEqual(files, again)
        self.assertEqual(len([c for c in calls if "googleapis" in c]), 1)
        self.assertTrue(all(c.startswith("https://fonts.g") for c in calls))  # a stylesheet cannot point the download elsewhere
        self.assertEqual(files[2], files[0])  # no italic file was offered, so the regular one stands in

    def test_only_listed_google_fonts_are_ever_requested(self):
        with mock.patch.object(design, "DOWNLOADS_ENABLED", True), mock.patch.object(design, "_fetch", side_effect=AssertionError):
            self.assertIsNone(design.download_google("Not A Real Font"))

    def test_a_font_that_cannot_be_found_falls_back_without_failing(self):
        names = pdf_render.font_set("Nonexistent Sans", "sans")
        self.assertEqual(len(names), 4)

    def test_a_bullet_the_font_cannot_draw_becomes_a_plain_one(self):
        self.assertEqual(pdf_render._bullet(("Helvetica",) * 4, "▪"), "•")
        self.assertEqual(pdf_render._bullet(("Helvetica",) * 4, "–"), "–")


class Portfolio(unittest.TestCase):
    CSS = ("@import\"https://fonts.googleapis.com/css2?family=Unna:ital,wght@0,400&family=Roboto+Condensed&family=Volkhorn\";"
           "h1{font-family:Unna,serif;font-size:2rem}h2{font-family:Roboto Condensed,sans-serif}"
           "h3{font-family:Fira Sans Extra Condensed,sans-serif}h4{font-family:Fira Sans Extra Condensed,sans-serif}"
           ".main-column p,.main-column li{font-family:Merriweather,serif}")

    def test_the_stylesheet_gives_each_role_its_font(self):
        roles, imported = design.read_fonts_from_css(self.CSS)
        self.assertEqual(roles["Titles (h1, project names)"], "Unna")
        self.assertEqual(roles["Headings (h2)"], "Roboto Condensed")
        self.assertEqual(roles["Sub-headings (h3, h4)"], "Fira Sans Extra Condensed")
        self.assertEqual(roles["Body text"], "Merriweather")
        self.assertIn("Volkhorn", imported)

    def test_the_fallback_reading_matches_what_the_site_uses(self):
        self.assertEqual(list(design.PORTFOLIO_READ["roles"].values()),
                         ["Unna", "Roboto Condensed", "Fira Sans Extra Condensed", "Merriweather"])

    def test_only_the_portfolio_site_is_ever_read(self):
        with self.assertRaises(urllib.error.URLError):
            design._site_get("https://evil.example/")
        redirects = design._SameSiteRedirects()
        with self.assertRaises(urllib.error.URLError):
            redirects.redirect_request(mock.Mock(), None, 301, "", {}, "https://evil.example/x")
        with self.assertRaises(urllib.error.URLError):
            redirects.redirect_request(mock.Mock(), None, 301, "", {}, "http://jamiekerig.com/x")

    def test_checking_the_site_saves_what_it_found(self):
        page = '<html><link rel="stylesheet" href="/assets/a.css"></html>'

        def fake(url, limit=0):
            return ("https://jltkerig.github.io/", page) if url == design.PORTFOLIO_SITE else (url, self.CSS)
        with mock.patch.object(design, "_site_get", fake):
            result = design.check_portfolio()
        self.assertTrue(result["live"])
        self.assertEqual(design.portfolio()["roles"]["Body text"], "Merriweather")
        design.reset()

    def test_the_preset_uses_the_sites_fonts(self):
        preset = design.portfolio_preset()
        self.assertEqual((preset["name_font"], preset["heading_font"], preset["font_family"]), ("Unna", "Roboto Condensed", "Merriweather"))
        self.assertEqual(preset["font_kind"], "serif")
        merge_layout(preset)  # valid


class DesignPage(unittest.TestCase):
    def setUp(self):
        web.app.config["TESTING"] = True
        self.client = web.app.test_client()
        design._file().unlink(missing_ok=True)
        with self.client.session_transaction() as s:
            s["csrf"] = "t"
        self.client.post("/upload", data={"csrf": "t", "resume": (BytesIO(pdf_bytes()), "my resume.pdf")},
                         content_type="multipart/form-data")
        patcher = mock.patch.object(jobfinder_db, "get_profile", side_effect=jobfinder_db.JobFinderUnavailable("tests have no database"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def form(self, **changes):
        base = {"csrf": "t", "font_family": "Merriweather", "name_font": "Unna", "heading_font": "Roboto Condensed", "detail_font": "",
                "name_size": "26", "heading_size": "12", "body_size": "10", "line_spacing": "1.35", "section_gap": "20",
                "page_size": "letter", "margin_top": "0.5", "margin_right": "0.6", "margin_bottom": "", "margin_left": "0.7",
                "name_align": "left", "contact_separator": "•", "heading_case": "upper", "bullet_char": "▪",
                "accent_color": "#353535", "text_color": "#353535", "heading_rule": "on", "notes": "My notes."}
        return {**base, **changes}

    def test_the_page_writes_out_the_design_and_lists_the_fonts(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("Résumé Design", html)
        self.assertIn("What It Sees", html)
        self.assertIn("SUMMARY → EXPERIENCE → SKILLS", html)
        self.assertIn('<option value="Unna" label="Google Font">', html)
        self.assertIn("Type on jamiekerig.com", html)

    def test_the_design_collapses_and_sits_below_the_two_side_by_side_cards(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('<details class="section-collapse" id="design-panel"', html)
        self.assertNotIn('id="design-panel" open', html)  # closed until the person (or a link to #design) opens it
        self.assertLess(html.index('id="documents"'), html.index('id="design"'))
        self.assertLess(html.index('id="design"'), html.index('id="profile"'))
        # Your Current Résumé and Reference Documents are plain cards next to each other, with nothing wide between them
        between = html[html.index("Your Current Résumé</h2>"):html.index('id="documents"')]
        self.assertNotIn('class="card wide"', between)

    def test_how_to_use_it_is_four_numbered_steps_with_titles(self):
        html = self.client.get("/").get_data(as_text=True)
        block = html[html.index('class="card wide how-to"'):html.index('id="documents"')]
        self.assertEqual(block.count('class="step-number"'), 4)
        for title in ("Upload Your Résumé", "Check Your Profile", "Ask Claude Desktop", "Review and Download"):
            self.assertIn(f"<h3>{title}</h3>", block)
        self.assertIn("how-to-status", block)  # the Claude Desktop connection shows beside the heading

    def test_the_profile_collapses_and_starts_open(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('<details class="section-collapse" id="profile-panel"', html)
        self.assertNotIn('<details class="section-collapse" id="profile-panel" open', html)  # the page script opens it unless you closed it
        self.assertIn('"resumeBuilder.profileOpen", "#profile", true', html)
        self.assertRegex(html, r'(?s)id="profile-panel".*?</details>\s*</section>\s*(?:<!--.*?-->\s*)?(?:\{% [^%]*%\}\s*)*<section class="card wide" id="references"')

    def test_the_portfolio_button_fills_the_form_without_saving(self):
        html = self.client.get("/?preset=portfolio").get_data(as_text=True)
        self.assertIn('value="Unna"', html)
        self.assertIn("not saved yet", html)
        self.assertEqual(design.overrides(), {})

    def test_saving_keeps_the_design_and_the_notes(self):
        reply = self.client.post("/design/save", data=self.form(), follow_redirects=True)
        self.assertIn("Saved your design", reply.get_data(as_text=True))
        saved = design.load()
        self.assertEqual(saved["overrides"]["name_font"], "Unna")
        self.assertEqual(saved["overrides"]["margin_left"], 0.7)
        self.assertEqual(saved["overrides"]["bullet_char"], "▪")
        self.assertNotIn("margin_bottom", saved["overrides"])  # left empty: the general margin applies
        self.assertEqual(saved["notes"], "My notes.")
        self.assertIn("My notes.", self.client.get("/").get_data(as_text=True))

    def test_new_résumés_use_the_saved_design(self):
        self.client.post("/design/save", data=self.form(body_size="9.5", margin_left="0.65"))
        record = builds.save_build("resume", CONTENT.model_dump(), job_title="Test")
        self.assertEqual(record["layout"]["body_size"], 9.5)
        self.assertEqual(record["layout"]["margin_left"], 0.65)
        self.assertEqual(record["layout"]["heading_font"], "Roboto Condensed")
        # what a caller passes for one résumé still wins
        self.assertEqual(builds.save_build("resume", CONTENT.model_dump(), job_title="Test2", layout={"body_size": 11})["layout"]["body_size"], 11)

    def test_a_missing_font_is_saved_with_suggestions(self):
        reply = self.client.post("/design/save", data=self.form(name_font="Gotham"), follow_redirects=True).get_data(as_text=True)
        self.assertIn("Not found: Gotham", reply)
        self.assertIn("Montserrat", reply)  # the suggestion shows beside the font

    def test_bad_values_are_refused_and_nothing_is_saved(self):
        for changes in ({"body_size": "99"}, {"body_size": "big"}, {"name_font": "<script>"}, {"font_family": ""}, {"margin_left": "5"},
                        {"bullet_char": "*"}, {"page_size": "tabloid"}, {"accent_color": "red"}):
            reply = self.client.post("/design/save", data=self.form(**changes), follow_redirects=True).get_data(as_text=True)
            self.assertIn("Design not saved", reply, changes)
        self.assertEqual(design.overrides(), {})

    def test_reset_returns_to_the_measured_design(self):
        self.client.post("/design/save", data=self.form())
        self.client.post("/design/reset", data={"csrf": "t"})
        self.assertEqual(design.overrides(), {})

    def test_looking_again_rewrites_the_notes(self):
        self.client.post("/design/save", data=self.form(notes="Old notes"))
        self.client.post("/design/reread", data={"csrf": "t"})
        self.assertIn("Margins:", design.load()["notes"])
        self.assertEqual(design.overrides()["name_font"], "Unna")  # settings stay; only the written notes are redone

    def test_the_site_check_survives_a_failure(self):
        with mock.patch.object(design, "_site_get", side_effect=urllib.error.URLError("offline")):
            reply = self.client.post("/design/portfolio", data={"csrf": "t"}, follow_redirects=True).get_data(as_text=True)
        self.assertIn("Couldn", reply)

    def test_the_preview_is_a_pdf_in_the_saved_design(self):
        self.client.post("/design/save", data=self.form(page_size="a4"))
        reply = self.client.get("/design/preview.pdf")
        self.assertEqual(reply.mimetype, "application/pdf")
        path = TEMP / "preview.pdf"
        path.write_bytes(reply.data)
        self.assertEqual(design_report.analyze_pdf(path)["page"], "A4")

    def test_posts_need_the_form_token(self):
        for url in ("/design/save", "/design/reset", "/design/reread", "/design/portfolio"):
            self.assertEqual(self.client.post(url, data={}).status_code, 400)

    def test_the_connector_shows_the_edited_design_and_notes(self):
        import mcp_server
        self.client.post("/design/save", data=self.form(notes="Keep headings in capitals."))
        parts = mcp_server.get_current_resume()
        summary = json.loads(parts[0])
        self.assertEqual(summary["layout"]["name_font"], "Unna")
        self.assertEqual(summary["design_notes"], "Keep headings in capitals.")


class OldDraftsAndDefaults(unittest.TestCase):
    def test_an_old_layout_without_the_new_settings_still_draws(self):
        old = {k: DEFAULT_LAYOUT[k] for k in ("font_family", "font_kind", "body_size", "name_size", "heading_size", "accent_color",
                                              "text_color", "name_align", "heading_case", "heading_rule", "margin_in")}
        self.assertTrue(pdf_bytes(old).startswith(b"%PDF"))

    def test_margins_land_where_they_are_set(self):
        facts = design_report.analyze_pdf(pdf_file(merge_layout({"margin_in": 1.0, "margin_left": None})))
        self.assertAlmostEqual(facts["margins"]["left"], 1.0, delta=0.04)


class HeadingCase(unittest.TestCase):
    SMALL = {"a", "an", "the", "and", "but", "or", "nor", "for", "of", "in", "on", "at", "to", "by", "as", "from", "with", "per"}

    def test_every_heading_in_this_app_is_in_title_case(self):
        bad = []
        for path in (Path(__file__).resolve().parents[1] / "templates").glob("*.html"):
            source = path.read_text(encoding="utf-8")
            for inner in re.findall(r"<h[1-6][^>]*>(.*?)</h[1-6]>", source, re.S):
                text = re.sub(r"\{\{.*?\}\}|\{%.*?%\}|<[^>]+>", " ", inner)
                words = re.findall(r"[A-Za-zÀ-ÿ][\wÀ-ÿ'’.]*", text)
                for n, word in enumerate(words):
                    if word[0].islower() and word.lower() not in self.SMALL and "." not in word:
                        bad.append(f"{path.name}: {text.strip()}")
                    elif word[0].islower() and n in (0, len(words) - 1) and "." not in word:
                        bad.append(f"{path.name}: {text.strip()}")
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()


class PageTitles(unittest.TestCase):
    def test_titles_end_with_bar_job_finder(self):
        base = (Path(__file__).resolve().parents[1] / "templates" / "base.html").read_text(encoding="utf-8")
        build = (Path(__file__).resolve().parents[1] / "templates" / "build.html").read_text(encoding="utf-8")
        self.assertIn("{% block title %}Résumé Builder | Job Finder{% endblock %}", base)
        self.assertIn("| Résumé Builder | Job Finder{% endblock %}", build)

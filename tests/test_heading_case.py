import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SMALL = {"a", "an", "the", "and", "but", "or", "nor", "for", "of", "in", "on", "at", "to", "by", "as", "from", "with", "per"}


class HeadingCase(unittest.TestCase):
    def test_every_heading_is_in_title_case(self):
        bad = []
        for path in (ROOT / "templates").glob("*.html"):
            source = path.read_text(encoding="utf-8")
            for inner in re.findall(r"<h[1-6][^>]*>(.*?)</h[1-6]>", source, re.S):
                text = re.sub(r"\{\{.*?\}\}|\{%.*?%\}|<[^>]+>", " ", inner)
                words = re.findall(r"[A-Za-zÀ-ÿ][\wÀ-ÿ'’.]*", text)
                for n, word in enumerate(words):
                    small = word.lower() in SMALL and n not in (0, len(words) - 1)
                    if word[0].islower() and not small and "." not in word:
                        bad.append(f"{path.name}: {text.strip()}")
        self.assertEqual(sorted(set(bad)), [])

    def test_script_made_headings_are_in_title_case(self):
        script = (ROOT / "static" / "js" / "charts.js").read_text(encoding="utf-8")
        for heading in re.findall(r'suggestionChips\([^,]+, "([^"]+)"', script):
            self.assertTrue(all(w[0].isupper() or w.lower() in SMALL for w in heading.rstrip(":").split()), heading)


if __name__ == "__main__":
    unittest.main()

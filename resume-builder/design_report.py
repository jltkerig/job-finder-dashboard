"""Look at the uploaded résumé's first page and write out its design: page, margins, header, headings, text,
spacing, bullets and dates. The numbers also become starting values for the Résumé Design settings."""
import re
import statistics
from collections import Counter

import pymupdf as fitz

import resume_file

BULLETS = {"•": "•", "●": "•", "": "•", "·": "·", "▪": "▪", "■": "▪", "◦": "○", "○": "○", "–": "–", "-": "-", "›": "›"}
INVISIBLE = dict.fromkeys(map(ord, "​‌‍﻿ "), " ")
CONTACT = re.compile(r"@|https?://|www\.|\.com|\(\d{3}\)|\d{3}[-. ]\d{4}|linkedin|portfolio:", re.I)
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
SEPARATORS = "|•·/"
ORDINAL = ("first", "second", "third", "fourth", "fifth")


def _bold(span):
    return bool(span["flags"] & 16) or "bold" in span["font"].lower()


def _italic(span):
    return bool(span["flags"] & 2) or "italic" in span["font"].lower() or "oblique" in span["font"].lower()


def _page_name(width, height):
    for name, (w, h) in {"Letter": (612, 792), "A4": (595, 842), "Legal": (612, 1008)}.items():
        if abs(width - w) < 4 and abs(height - h) < 4:
            return name
    return f"{width / 72:.1f} x {height / 72:.1f} in"


def _rule_color(rule):
    """#RRGGBB for a drawn rule (stroke or fill), or None."""
    color = (rule or {}).get("color") or (rule or {}).get("fill")
    return "#{:02X}{:02X}{:02X}".format(*(round(c * 255) for c in color[:3])) if color and len(color) >= 3 else None


def _case(text):
    letters = [c for c in text if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return "upper"
    words = [w for w in re.findall(r"[A-Za-z]+", text) if len(w) > 3]
    return "title" if words and all(w[0].isupper() for w in words) else "sentence"


def analyze_pdf(pdf_path):
    """Facts about the first page. Raises nothing for odd files; unknown things come back as None."""
    with fitz.open(pdf_path) as doc:
        page = doc[0]
        width, height, pages = page.rect.width, page.rect.height, doc.page_count
        blocks = [b for b in page.get_text("dict")["blocks"] if b.get("lines")]
        rules = [d for d in page.get_drawings() if d["rect"].height < 2.5 and d["rect"].width > width * 0.3]
        later = [(("".join(s["text"] for s in line["spans"])).translate(INVISIBLE).strip(), resume_file.family_name(line["spans"][0]["font"]),
                  round(line["spans"][0]["size"] * 2) / 2, _bold(line["spans"][0]))
                 for extra in list(doc)[1:6] for block in extra.get_text("dict")["blocks"] for line in block.get("lines", [])
                 if line["spans"]]
    lines = []
    for number, block in enumerate(blocks):
        for line in block["lines"]:
            spans = []
            for s in line["spans"]:
                s["text"] = s["text"].translate(INVISIBLE)
                if s["text"].strip():
                    spans.append(s)
            if spans:
                lines.append({"block": number, "spans": spans, "bbox": line["bbox"],
                              "text": "".join(s["text"] for s in spans).strip(), "origin": spans[0]["origin"][1]})
    if not lines:
        return None
    lines.sort(key=lambda l: (l["bbox"][1], l["bbox"][0]))

    sizes = Counter()
    fonts = {}
    for line in lines:
        for s in line["spans"]:
            sizes[round(s["size"] * 2) / 2] += len(s["text"])
            entry = fonts.setdefault(resume_file.family_name(s["font"]), {"chars": 0, "sizes": set()})
            entry["chars"] += len(s["text"])
            entry["sizes"].add(round(s["size"] * 2) / 2)
    body_size = sizes.most_common(1)[0][0]
    biggest = max(lines, key=lambda l: max(s["size"] for s in l["spans"]))
    body_lines = [l for l in lines if abs(l["spans"][0]["size"] - body_size) < 0.6]
    body_span = Counter((resume_file.family_name(l["spans"][0]["font"]), resume_file._hex(l["spans"][0]["color"]))
                        for l in body_lines).most_common(1)[0][0] if body_lines else ("", "")

    def candidate(line):
        first = line["spans"][0]
        return (line is not biggest and len(line["text"]) <= 40 and bool(re.search(r"[A-Za-z]", line["text"]))
                and not CONTACT.search(line["text"]) and bool(line["bbox"][1] > biggest["bbox"][3])
                and (first["size"] >= body_size + 0.75 or (_bold(first) and line["text"].isupper())))

    def look_key(line):
        s0 = line["spans"][0]
        return (resume_file.family_name(s0["font"]), round(s0["size"] * 2) / 2, _bold(s0))
    groups = Counter(look_key(l) for l in lines if candidate(l))
    # Section headings: the largest look used at least twice (or the most used one); entry titles: the next look down.
    ranked = sorted(groups, key=lambda k: (-(groups[k] >= 2), -k[1], -groups[k]))
    section_key = ranked[0] if ranked else None
    entry_key = next((k for k in ranked[1:] if k[1] < section_key[1] or k[0] != section_key[0]), None) if section_key else None
    headings = [l for l in lines if section_key and candidate(l) and look_key(l) == section_key]
    later_headings = [t for t, family, size, bold in later if section_key and (family, size, bold) == section_key
                      and t and len(t) <= 40 and not CONTACT.search(t)]
    entries = [l for l in lines if entry_key and candidate(l) and look_key(l) == entry_key]

    left = min(l["bbox"][0] for l in lines)
    right = width - max(l["bbox"][2] for l in lines)
    top = min(l["bbox"][1] for l in lines)
    gap_bottom = height - max(l["bbox"][3] for l in lines)
    center = (biggest["bbox"][0] + biggest["bbox"][2]) / 2

    # Header: everything between the name and the first heading.
    first_heading_y = headings[0]["bbox"][1] if headings else height
    header = [l for l in lines if l is not biggest and biggest["bbox"][3] - 1 <= l["bbox"][1] < first_heading_y]
    joined = " ".join(l["text"] for l in header)
    separators = Counter(c for c in joined if c in SEPARATORS and re.search(rf"\s\{c}\s", joined))
    header_rule = next((r for r in rules if biggest["bbox"][3] <= r["rect"].y0 <= first_heading_y), None)
    heading_rules = [r for r in rules if any(0 <= r["rect"].y0 - h["bbox"][3] <= 8 for h in headings)]
    any_rule = heading_rules[0] if heading_rules else header_rule

    # Line spacing: baseline distance between neighbouring body lines of the same block.
    gaps = []
    for number in {l["block"] for l in body_lines}:
        same = sorted((l for l in body_lines if l["block"] == number), key=lambda l: l["origin"])
        gaps += [b["origin"] - a["origin"] for a, b in zip(same, same[1:]) if 0 < b["origin"] - a["origin"] < body_size * 2.6]
    spacing = round(statistics.median(gaps) / body_size / 0.05) * 0.05 if gaps else None

    # Space above headings, beyond a normal line.
    before = []
    for h in headings:
        above = [l for l in lines if l["bbox"][3] <= h["bbox"][1] + 0.5 and l is not h]
        if above:
            before.append(h["bbox"][1] - max(l["bbox"][3] for l in above))
    section_gap = round(statistics.median(before)) if before else None

    # Bullets: lines that start with a bullet character (Word's symbol bullet comes through as a private character).
    bullets = Counter()
    indents = []
    for l in lines:
        first = l["spans"][0]["text"].strip()
        if first and first[0] in BULLETS and (len(first) == 1 or first[1:2].isspace() or len(l["spans"]) > 1):
            bullets[BULLETS[first[0]]] += 1
            indents.append(l["bbox"][0] - left)

    # Two columns: many lines start well to the right while others start at the left margin.
    right_start = sum(1 for l in lines if l["bbox"][0] > width * 0.45 and l["bbox"][2] - l["bbox"][0] < width * 0.45)
    left_start = sum(1 for l in lines if l["bbox"][0] < width * 0.2 and l["bbox"][2] < width * 0.62)
    columns = 2 if right_start >= 6 and left_start >= 6 and right_start >= len(lines) * 0.25 else 1

    dated = [l for l in lines if YEAR.search(l["text"])]
    dates_right = sum(1 for l in dated if l["bbox"][0] > width * 0.55 and l["bbox"][2] >= width - right - 36)

    first_name = biggest["spans"][0]
    def look(line):
        s = line["spans"][0]
        return {"font": resume_file.family_name(s["font"]), "size": round(s["size"] * 2) / 2,
                "color": resume_file._hex(s["color"]), "bold": _bold(s), "italic": _italic(s)}
    heading_looks = [look(h) for h in headings]
    return {
        "page": _page_name(width, height), "pages": pages,
        "margins": {"left": round(left / 72, 2), "right": round(right / 72, 2), "top": round(top / 72, 2),
                    "bottom": round(gap_bottom / 72, 2) if gap_bottom < 1.5 * 72 else None},
        "columns": columns,
        "name": {"text": biggest["text"], **look(biggest), "align": "center" if abs(center - width / 2) < width * 0.08 else "left"},
        "header": {"lines": len(header), "size": look(header[0])["size"] if header else None,
                   "font": look(header[0])["font"] if header else None,
                   "separator": separators.most_common(1)[0][0] if separators else None,
                   "rule": bool(header_rule), "sample": header[0]["text"][:80] if header else ""},
        "headings": {"items": [h["text"] for h in headings] + later_headings,
                     "case": Counter(_case(h["text"]) for h in headings).most_common(1)[0][0] if headings else None,
                     "font": Counter(x["font"] for x in heading_looks).most_common(1)[0][0] if heading_looks else None,
                     "size": Counter(x["size"] for x in heading_looks).most_common(1)[0][0] if heading_looks else None,
                     "color": Counter(x["color"] for x in heading_looks).most_common(1)[0][0] if heading_looks else None,
                     "bold": sum(x["bold"] for x in heading_looks) * 2 >= len(heading_looks) if heading_looks else None,
                     "rule": bool(heading_rules),
                     "rule_color": _rule_color(any_rule)},
        "entries": ({"count": len(entries), **look(entries[0])} if entries else None),
        "body": {"font": body_span[0], "size": body_size, "color": body_span[1], "spacing": spacing},
        "section_gap": section_gap,
        "bullets": {"char": bullets.most_common(1)[0][0] if bullets else None, "count": sum(bullets.values()),
                    "indent": round(statistics.median(indents) / 72, 2) if indents else None},
        "dates": {"count": len(dated), "right": dates_right},
        "emphasis": {"bold": sum(_bold(s) for l in lines for s in l["spans"]),
                     "italic": sum(_italic(s) for l in lines for s in l["spans"])},
        "fonts": [{"font": f, "chars": v["chars"], "sizes": sorted(v["sizes"])} for f, v in
                  sorted(fonts.items(), key=lambda kv: -kv[1]["chars"])],
    }


def layout_from_facts(facts, layout):
    """The extra layout settings this file shows, to add to what resume_file measured."""
    if not facts:
        return {}
    out = {"page_size": "a4" if facts["page"] == "A4" else "letter"}
    for side in ("left", "right", "top", "bottom"):
        if facts["margins"].get(side):
            out[f"margin_{side}"] = min(1.5, max(0.3, facts["margins"][side]))
    if facts["body"]["spacing"]:
        out["line_spacing"] = min(1.8, max(1.0, facts["body"]["spacing"]))
    if facts["section_gap"]:
        out["section_gap"] = min(40, max(2, facts["section_gap"]))
    if facts["bullets"]["char"] in ("•", "–", "-", "·", "▪", "○", "›"):
        out["bullet_char"] = facts["bullets"]["char"]
    if facts["header"]["separator"] in ("|", "•", "·", "/"):
        out["contact_separator"] = facts["header"]["separator"]
    out["header_rule"] = facts["header"]["rule"]
    hd = facts["headings"]
    if hd["size"]:
        out["heading_size"] = min(24, max(8, hd["size"]))
        out["heading_case"] = "upper" if hd["case"] == "upper" else "title"
        out["heading_rule"] = hd["rule"]
        if hd["color"]:
            out["accent_color"] = hd["color"]
    if facts["name"]["font"] != facts["body"]["font"]:
        out["name_font"] = facts["name"]["font"]
    if facts["headings"]["font"] and facts["headings"]["font"] != facts["body"]["font"]:
        out["heading_font"] = facts["headings"]["font"]
    return out


def _inches(value):
    return f"{value:.2f}".rstrip("0").rstrip(".") + " in"


def describe(facts, layout):
    """[(label, sentence)] describing the design. Works from the settings alone when there are no page facts."""
    rows = []
    if not facts:
        rows.append(("Page", "Only the document's styles could be read (Word was not available), so these are the settings it set."))
        rows.append(("Margins", f"{_inches(layout['margin_in'])} all round."))
        rows.append(("Name", f"{layout['font_family']}, {layout['name_size']:g} pt, {layout['name_align']}-aligned, color {layout['accent_color']}."))
        rows.append(("Section Headings", f"{layout['heading_size']:g} pt, {'in capitals' if layout['heading_case'] == 'upper' else 'as written'}"
                     f"{', with a line under each' if layout['heading_rule'] else ''}."))
        rows.append(("Body Text", f"{layout['font_family']}, {layout['body_size']:g} pt, color {layout['text_color']}."))
        return rows
    m = facts["margins"]
    rows.append(("Page", f"{facts['page']} paper, {facts['pages']} page{'s' if facts['pages'] != 1 else ''}, "
                         f"{'two columns' if facts['columns'] == 2 else 'a single column'}."))
    rows.append(("Margins", f"Left {_inches(m['left'])}, right {_inches(m['right'])}, top {_inches(m['top'])}"
                            + (f", bottom {_inches(m['bottom'])}." if m["bottom"] else ". The page is not full, so the bottom margin cannot be measured.")))
    n = facts["name"]
    rows.append(("Name", f"“{n['text']}” in {n['font']}{' bold' if n['bold'] else ''}, {n['size']:g} pt, {n['align']}-aligned, color {n['color']}."))
    h = facts["header"]
    if h["lines"]:
        rows.append(("Header and Contact", f"{h['lines']} line{'s' if h['lines'] != 1 else ''} under the name in {h['font']}, {h['size']:g} pt"
                     + (f", items separated by “{h['separator']}”" if h["separator"] else "")
                     + (", with a line under the header." if h["rule"] else ", with no line under the header.")))
    else:
        rows.append(("Header and Contact", "No contact lines were found under the name."))
    hd = facts["headings"]
    if hd["items"]:
        case = {"upper": "in capitals", "title": "in Title Case", "sentence": "in sentence case"}[hd["case"]]
        rows.append(("Section Headings", f"{len(hd['items'])} headings, {case}, {hd['font']}{' bold' if hd['bold'] else ''}, {hd['size']:g} pt, "
                     f"color {hd['color']}" + (f", each with a line under it ({hd['rule_color']})." if hd["rule"] else ", with no lines under them.")))
        rows.append(("Section Order", " → ".join(hd["items"])))
    en = facts.get("entries")
    if en:
        rows.append(("Job and Entry Titles", f"{en['count']} titles in {en['font']}{' bold' if en['bold'] else ''}, {en['size']:g} pt, color {en['color']}."))
    b = facts["body"]
    rows.append(("Body Text", f"{b['font']}, {b['size']:g} pt, color {b['color']}"
                 + (f", line height {b['spacing']:.2f} times the text size." if b["spacing"] else ".")))
    if facts["section_gap"]:
        rows.append(("Spacing", f"About {facts['section_gap']} pt of space above each section heading."))
    bl = facts["bullets"]
    rows.append(("Bullets", f"{bl['count']} bullet points using “{bl['char']}”" + (f", indented {_inches(bl['indent'])}." if bl["indent"] else ".")
                 if bl["count"] else "No bullet points were found."))
    d = facts["dates"]
    if d["count"]:
        rows.append(("Dates", "Dates sit at the right edge of the line." if d["right"] * 2 >= d["count"] else "Dates follow the text on the same line."))
    e = facts["emphasis"]
    rows.append(("Bold and Italic", f"{e['bold']} bold and {e['italic']} italic pieces of text."))
    rows.append(("Fonts Used", "; ".join(f"{f['font']} ({', '.join(f'{s:g}' for s in f['sizes'])} pt)" for f in facts["fonts"][:6])))
    return rows


def notes_text(rows):
    return "\n".join(f"{label}: {text}" for label, text in rows)


def fonts_seen(facts):
    return [f["font"] for f in (facts or {}).get("fonts", [])]

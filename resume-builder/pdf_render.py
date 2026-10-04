"""Draw resumes and cover letters as PDFs in the layout measured from the user's resume."""
import os
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

import design
from models import DEFAULT_LAYOUT, CoverLetterContent, ResumeContent

FONT_DIRS = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
             Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"]
# Regular, bold, italic, bold italic file names in the Windows font folders.
FONT_FILES = {
    "Arial": ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"),
    "Calibri": ("calibri.ttf", "calibrib.ttf", "calibrii.ttf", "calibriz.ttf"),
    "Cambria": ("cambria.ttc", "cambriab.ttf", "cambriai.ttf", "cambriaz.ttf"),
    "Georgia": ("georgia.ttf", "georgiab.ttf", "georgiai.ttf", "georgiaz.ttf"),
    "Times New Roman": ("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf"),
    "Garamond": ("GARA.TTF", "GARABD.TTF", "GARAIT.TTF", "GARABD.TTF"),
    "Verdana": ("verdana.ttf", "verdanab.ttf", "verdanai.ttf", "verdanaz.ttf"),
    "Tahoma": ("tahoma.ttf", "tahomabd.ttf", "tahoma.ttf", "tahomabd.ttf"),
    "Segoe UI": ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "segoeuiz.ttf"),
    "Trebuchet MS": ("trebuc.ttf", "trebucbd.ttf", "trebucit.ttf", "trebucbi.ttf"),
    "Book Antiqua": ("BKANT.TTF", "ANTQUAB.TTF", "ANTQUAI.TTF", "ANTQUABI.TTF"),
    "Century Gothic": ("GOTHIC.TTF", "GOTHICB.TTF", "GOTHICI.TTF", "GOTHICBI.TTF"),
    "Palatino Linotype": ("pala.ttf", "palab.ttf", "palai.ttf", "palabi.ttf"),
    "Aptos": ("Aptos.ttf", "Aptos-Bold.ttf", "Aptos-Italic.ttf", "Aptos-Bold-Italic.ttf"),
}
BUILTIN = {"serif": ("Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic"),
           "sans": ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique")}
_registered = {}
_not_found = set()
FRAME_PADDING = 6  # points the page frame keeps inside each margin


def _find_font_file(name):
    for folder in FONT_DIRS:
        path = folder / name
        if path.exists():
            return path
    return None


def available_fonts():
    return sorted(f for f, files in FONT_FILES.items() if all(_find_font_file(n) for n in files))


def _register(family, paths):
    names = tuple(f"{family}{suffix}" for suffix in ("", "-Bold", "-Italic", "-BoldItalic"))
    for name, path in zip(names, paths):
        pdfmetrics.registerFont(TTFont(name, str(path), subfontIndex=0))
    pdfmetrics.registerFontFamily(family, normal=names[0], bold=names[1], italic=names[2], boldItalic=names[3])
    _registered[family] = names
    return names


def font_set(family, kind):
    """Register a Windows or Google font family once; fall back to a PDF built-in font of the same kind."""
    if family in _registered:
        return _registered[family]
    files = FONT_FILES.get(family)
    paths = [_find_font_file(n) for n in files] if files else [None]
    if all(paths):
        return _register(family, paths)
    if family in design.GOOGLE_FONTS and family not in _not_found:
        google = design.download_google(family)
        if google:
            return _register(family, google)
        _not_found.add(family)  # offline or blocked: do not try again until the app restarts
    fallback = "Times New Roman" if kind == "serif" else "Arial"
    if family != fallback and fallback in FONT_FILES and all(_find_font_file(n) for n in FONT_FILES[fallback]):
        return font_set(fallback, kind)
    return BUILTIN["serif" if kind == "serif" else "sans"]


def _full(layout):
    """Every layout setting, with the defaults filling anything an older draft does not have."""
    return {**DEFAULT_LAYOUT, **{k: v for k, v in (layout or {}).items() if v is not None}}


def _role_font(layout, key):
    """The four font names for a role (name, heading or detail); the body font when none is chosen."""
    family = layout.get(key) or layout["font_family"]
    return font_set(family, design.font_kind(family) if layout.get(key) else layout["font_kind"])


def _bullet(font_names, char):
    """The chosen bullet when the font can draw it; a plain bullet otherwise."""
    try:
        has = ord(char) in pdfmetrics.getFont(font_names[0]).face.charToGlyph
    except AttributeError:  # a built-in PDF font: it draws these
        has = char in "•–-·"
    return char if has else "•"


def _styles(layout):
    layout = _full(layout)
    regular, bold, italic, _ = font_set(layout["font_family"], layout["font_kind"])
    name_bold = _role_font(layout, "name_font")[1]
    heading_bold = _role_font(layout, "heading_font")[1]
    detail, _, detail_italic, _ = _role_font(layout, "detail_font")
    size = layout["body_size"]
    text = colors.HexColor(layout["text_color"])
    accent = colors.HexColor(layout["accent_color"])
    align = TA_CENTER if layout["name_align"] == "center" else TA_LEFT
    lead = size * layout["line_spacing"]
    gap = layout["section_gap"] if layout["section_gap"] is not None else size
    return {
        "name": ParagraphStyle("name", fontName=name_bold, fontSize=layout["name_size"],
                               leading=layout["name_size"] * 1.15, textColor=accent, alignment=align),
        "headline": ParagraphStyle("headline", fontName=detail, fontSize=size + 1.5, leading=(size + 1.5) * 1.3,
                                   textColor=text, alignment=align, spaceBefore=2),
        "contact": ParagraphStyle("contact", fontName=detail, fontSize=size - 0.5, leading=lead,
                                  textColor=text, alignment=align, spaceBefore=3),
        "heading": ParagraphStyle("heading", fontName=heading_bold, fontSize=layout["heading_size"],
                                  leading=layout["heading_size"] * 1.2, textColor=accent, spaceBefore=gap, spaceAfter=2),
        "body": ParagraphStyle("body", fontName=regular, fontSize=size, leading=lead, textColor=text),
        "item": ParagraphStyle("item", fontName=regular, fontSize=size, leading=lead, textColor=text, spaceBefore=size * 0.5),
        "sub": ParagraphStyle("sub", fontName=detail_italic, fontSize=size, leading=lead, textColor=text),
        "dates": ParagraphStyle("dates", fontName=detail, fontSize=size, leading=lead, textColor=text,
                                alignment=TA_RIGHT, spaceBefore=size * 0.5),
        "bullet": ParagraphStyle("bullet", fontName=regular, fontSize=size, leading=lead, textColor=text,
                                 leftIndent=size * 1.4, bulletIndent=size * 0.4, spaceBefore=1, bulletFontName=regular),
        "letter": ParagraphStyle("letter", fontName=regular, fontSize=size + 0.5, leading=(size + 0.5) * 1.35,
                                 textColor=text, spaceAfter=size * 0.9),
    }, accent


def _p(text, style):
    return Paragraph(escape(text or "").replace("\n", "<br/>"), style)


def _header(content, layout, styles, accent, headline=""):
    parts = [_p(content.full_name, styles["name"])]
    if headline:
        parts.append(_p(headline, styles["headline"]))
    contact = [c.strip() for c in content.contact if c.strip()]
    if contact:
        parts.append(_p(f"  {layout['contact_separator']}  ".join(contact), styles["contact"]))
    if layout["header_rule"]:
        parts.append(HRFlowable(width="100%", thickness=1, color=accent, spaceBefore=6, spaceAfter=2))
    return parts


def _doc(path, layout, title, author):
    layout = _full(layout)
    legacy = min(layout["margin_in"], 0.75)  # top and bottom were never wider than 3/4 inch
    # The page frame pads each side by FRAME_PADDING, so take that off to put the text exactly at the margin.
    margin = lambda key, default: max((layout[key] if layout[key] is not None else default) * inch - FRAME_PADDING, 0)
    return SimpleDocTemplate(path if hasattr(path, "write") else str(path),
                             pagesize=A4 if layout["page_size"] == "a4" else LETTER,
                             leftMargin=margin("margin_left", layout["margin_in"]), rightMargin=margin("margin_right", layout["margin_in"]),
                             topMargin=margin("margin_top", legacy), bottomMargin=margin("margin_bottom", legacy),
                             title=title, author=author, creator="Resume Builder")


def render_resume(content: ResumeContent, layout: dict, path: Path):
    layout = _full(layout)
    styles, accent = _styles(layout)
    doc = _doc(path, layout, f"{content.full_name} - Resume", content.full_name)
    width = doc.width - 2 * FRAME_PADDING  # the page frame keeps some padding on each side
    bullet = _bullet(font_set(layout["font_family"], layout["font_kind"]), layout["bullet_char"])
    story = _header(content, layout, styles, accent, content.headline)
    for section in content.sections:
        blocks = []
        if section.text:
            blocks.append([_p(section.text, styles["body"])])
        for item in section.items:
            flow = []
            head = escape(item.heading)
            left = [Paragraph(f"<b>{head}</b>" if head else "", styles["item"])]
            sub = " — ".join(x for x in (item.subheading, item.location) if x)
            if sub:
                left.append(_p(sub, styles["sub"]))
            if item.dates:
                table = Table([[left, _p(item.dates, styles["dates"])]], colWidths=[width * 0.7, width * 0.3])
                table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                           ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                           ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
                flow.append(table)
            else:
                flow.extend(left)
            if item.text:
                flow.append(_p(item.text, styles["body"]))
            flow.extend(Paragraph(escape(b), styles["bullet"], bulletText=bullet) for b in item.bullets if b.strip())
            blocks.append(flow)
        _add_section(story, section.title, blocks, layout, styles, accent)
    if content.references:
        _add_section(story, "References", [_reference(r, layout, styles) for r in content.references],
                     layout, styles, accent)
    doc.build(story)


def _add_section(story, title, blocks, layout, styles, accent):
    if not blocks:
        return
    heading = [_p(title.upper() if layout["heading_case"] == "upper" else title, styles["heading"])]
    if layout["heading_rule"]:
        heading.append(HRFlowable(width="100%", thickness=0.6, color=accent, spaceBefore=0, spaceAfter=3))
    # Keep a heading with its first entry so it never sits alone at the bottom of a page.
    story.append(KeepTogether(heading + blocks[0]))
    for block in blocks[1:]:
        story.append(KeepTogether(block))


def _reference(ref, layout, styles):
    block = [Paragraph(f"<b>{escape(ref.name)}</b>", styles["item"])]
    role = ", ".join(x for x in (ref.job_title, ref.company) if x)
    if role:
        block.append(_p(role, styles["body"]))
    if ref.relationship and ref.user_job:
        known = f"{ref.relationship} while I was {ref.user_job}"
    else:
        known = ref.relationship or (f"Worked together when I was {ref.user_job}" if ref.user_job else "")
    if known:
        block.append(_p(known, styles["sub"]))
    reach = "  |  ".join(x for x in (ref.phone, ref.email) if x)
    if reach:
        block.append(_p(reach, styles["body"]))
    return block


def render_cover_letter(content: CoverLetterContent, layout: dict, path: Path):
    layout = _full(layout)
    styles, accent = _styles(layout)
    doc = _doc(path, layout, f"{content.full_name} - Cover Letter", content.full_name)
    story = _header(content, layout, styles, accent)
    story.append(Spacer(1, 14))
    today = date.today()
    story.append(_p(content.date or f"{today:%B} {today.day}, {today.year}", styles["letter"]))
    if content.recipient:
        story.append(_p("\n".join(content.recipient), styles["letter"]))
    story.append(_p(content.greeting, styles["letter"]))
    story.extend(_p(p, styles["letter"]) for p in content.paragraphs if p.strip())
    story.append(_p(content.closing, styles["body"]))
    story.append(Spacer(1, 26))
    story.append(_p(content.signature or content.full_name, styles["body"]))
    doc.build(story)

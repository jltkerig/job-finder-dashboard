"""Résumé design: the font list (Windows and Google Fonts), similar-font suggestions, the type on the portfolio
site, and the design the person saved. Only font names ever leave this computer (to Google Fonts), and only
jamiekerig.com is ever read."""
import json
import re
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import config

DOWNLOADS_ENABLED = True  # tests switch this off so they never use the network

# Google Fonts worth offering for a résumé, with a rough class used to suggest look-alikes.
GOOGLE_FONTS = {
    "Roboto": "sans", "Open Sans": "sans", "Lato": "sans", "Montserrat": "sans", "Source Sans 3": "sans",
    "Inter": "sans", "Poppins": "sans", "Nunito": "sans", "Nunito Sans": "sans", "Raleway": "sans",
    "Work Sans": "sans", "Mulish": "sans", "PT Sans": "sans", "Noto Sans": "sans", "IBM Plex Sans": "sans",
    "Rubik": "sans", "Karla": "sans", "DM Sans": "sans", "Jost": "sans", "Carlito": "sans", "Arimo": "sans",
    "Questrial": "sans", "Didact Gothic": "sans", "Libre Franklin": "sans",
    "Merriweather": "serif", "Lora": "serif", "Playfair Display": "serif", "Libre Baskerville": "serif",
    "EB Garamond": "serif", "Cormorant Garamond": "serif", "Crimson Pro": "serif", "Source Serif 4": "serif",
    "Noto Serif": "serif", "PT Serif": "serif", "IBM Plex Serif": "serif", "Unna": "serif", "Vollkorn": "serif",
    "Gelasio": "serif", "Tinos": "serif", "Caladea": "serif", "Averia Serif Libre": "serif", "Bitter": "serif",
    "Spectral": "serif",
    "Roboto Condensed": "condensed", "Fira Sans Extra Condensed": "condensed", "Oswald": "condensed",
    "Barlow Condensed": "condensed", "Archivo Narrow": "condensed", "Pragati Narrow": "condensed",
    "Sofia Sans Extra Condensed": "condensed",
    "Abril Fatface": "display", "Bebas Neue": "display", "Josefin Sans": "display",
    "Courier Prime": "mono", "IBM Plex Mono": "mono", "Roboto Mono": "mono", "Space Mono": "mono",
    "Caveat": "script", "Dancing Script": "script", "Great Vibes": "script",
}
SERIF_CLASSES = {"serif"}

# Fonts people have that are not free or not installed here, and the closest Google Fonts.
LOOK_ALIKES = {
    "helvetica": ["Arimo", "Roboto", "Inter"], "helvetica neue": ["Inter", "Arimo", "Roboto"],
    "arial": ["Arimo", "Roboto", "Open Sans"], "calibri": ["Carlito", "Lato", "Open Sans"],
    "cambria": ["Caladea", "Merriweather", "Source Serif 4"], "times": ["Tinos", "Libre Baskerville", "Lora"],
    "times new roman": ["Tinos", "Libre Baskerville", "Lora"], "georgia": ["Gelasio", "Merriweather", "Lora"],
    "garamond": ["EB Garamond", "Cormorant Garamond", "Crimson Pro"], "minion pro": ["Crimson Pro", "Source Serif 4", "EB Garamond"],
    "gotham": ["Montserrat", "Nunito Sans", "Work Sans"], "proxima nova": ["Montserrat", "Nunito Sans", "Mulish"],
    "futura": ["Jost", "Josefin Sans", "Questrial"], "avenir": ["Nunito Sans", "Mulish", "Lato"],
    "myriad pro": ["Source Sans 3", "Open Sans", "PT Sans"], "baskerville": ["Libre Baskerville", "Lora", "Spectral"],
    "didot": ["Playfair Display", "Libre Baskerville", "Abril Fatface"], "bodoni": ["Playfair Display", "Abril Fatface", "Libre Baskerville"],
    "century gothic": ["Didact Gothic", "Questrial", "Jost"], "trebuchet ms": ["Fira Sans Extra Condensed", "Karla", "Rubik"],
    "verdana": ["Open Sans", "Noto Sans", "Nunito Sans"], "tahoma": ["Noto Sans", "PT Sans", "Open Sans"],
    "segoe ui": ["Open Sans", "Noto Sans", "Source Sans 3"], "palatino": ["Libre Baskerville", "Spectral", "Lora"],
    "palatino linotype": ["Libre Baskerville", "Spectral", "Lora"], "book antiqua": ["Libre Baskerville", "Spectral", "EB Garamond"],
    "optima": ["Lato", "Nunito Sans", "Work Sans"], "gill sans": ["Lato", "Nunito Sans", "Josefin Sans"],
    "franklin gothic": ["Libre Franklin", "Roboto Condensed", "Work Sans"], "rockwell": ["Bitter", "Vollkorn", "Merriweather"],
    "constantia": ["Lora", "Source Serif 4", "Merriweather"], "candara": ["Nunito Sans", "Lato", "Mulish"],
    "corbel": ["Nunito Sans", "Lato", "Open Sans"], "aptos": ["Inter", "Open Sans", "Source Sans 3"],
    "sf pro": ["Inter", "Roboto", "Open Sans"], "san francisco": ["Inter", "Roboto", "Open Sans"],
    "lucida": ["Noto Sans", "PT Sans", "Open Sans"], "courier new": ["Courier Prime", "IBM Plex Mono", "Roboto Mono"],
    "courier": ["Courier Prime", "IBM Plex Mono", "Roboto Mono"],
}
FALLBACK_BY_KIND = {"serif": ["Lora", "Merriweather", "Source Serif 4"], "sans": ["Inter", "Open Sans", "Lato"],
                    "mono": ["Courier Prime", "IBM Plex Mono", "Roboto Mono"],
                    "condensed": ["Roboto Condensed", "Fira Sans Extra Condensed", "Oswald"],
                    "script": ["Caveat", "Dancing Script", "Great Vibes"]}

# The type on jamiekerig.com, as read from its stylesheet (h1, h2, h3/h4 and the article text).
PORTFOLIO_SITE = "https://jamiekerig.com"
PORTFOLIO_HOSTS = {"jamiekerig.com", "www.jamiekerig.com", "jltkerig.github.io"}
PORTFOLIO_READ = {
    "checked_at": "2026-10-02", "live": False,
    "roles": {"Titles (h1, project names)": "Unna", "Headings (h2)": "Roboto Condensed",
              "Sub-headings (h3, h4)": "Fira Sans Extra Condensed", "Body text": "Merriweather"},
    "accents": ["Volkhorn (quotes; no longer offered by Google Fonts)", "Courier Prime (code)", "Caveat (hand-written hello)"],
    "colors": {"Dark": "#353535", "Accent yellow": "#F7D136"},
}
# The résumé settings that follow from it. The site's yellow is too pale for white paper, so headings use the dark.
PORTFOLIO_PRESET = {
    "font_family": "Merriweather", "font_kind": "serif", "name_font": "Unna", "heading_font": "Roboto Condensed",
    "detail_font": "Fira Sans Extra Condensed", "text_color": "#353535", "accent_color": "#353535",
    "heading_case": "upper", "heading_rule": True, "body_size": 10, "name_size": 26, "heading_size": 12,
    "line_spacing": 1.35,
}
PORTFOLIO_ROLES = (("Titles (h1, project names)", "h1"), ("Headings (h2)", "h2"), ("Sub-headings (h3, h4)", "h3"))


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def font_kind(name):
    """'serif' or 'sans' for a font name (used when a font has to be replaced by a built-in one)."""
    import resume_file
    if name in resume_file.SERIF_FAMILIES or GOOGLE_FONTS.get(name) in SERIF_CLASSES:
        return "serif"
    lower = name.lower()
    return "serif" if "sans" not in lower and re.search(r"serif|garamond|baskerville|times|georgia|roman", lower) else "sans"


# --- Google Fonts: download once, keep in data/fonts -------------------------------------------------------

STYLES = ("regular", "bold", "italic", "bolditalic")


def _google_dir(family):
    return config.FONT_DIR / slug(family)


def cached_google_files(family):
    """The four cached TTF paths (missing styles borrow the nearest one), or None when nothing is cached."""
    folder = _google_dir(family)
    found = {style: folder / f"{style}.ttf" for style in STYLES if (folder / f"{style}.ttf").exists()}
    if "regular" not in found:
        return None
    regular = found["regular"]
    bold = found.get("bold", regular)
    italic = found.get("italic", regular)
    return regular, bold, italic, found.get("bolditalic", found.get("italic", bold))


def _fetch(url, limit=2_000_000):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/4.0"})  # an old agent gets plain .ttf files
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read(limit + 1)[:limit]


def _css_files(css):
    """{(style, weight): url} from a Google Fonts stylesheet."""
    files = {}
    for block in re.findall(r"@font-face\s*\{[^}]*\}", css):
        style = re.search(r"font-style:\s*(\w+)", block)
        weight = re.search(r"font-weight:\s*(\d+)", block)
        url = re.search(r"url\((https://fonts\.gstatic\.com/[^)]+)\)", block)
        if style and weight and url:
            files.setdefault((style.group(1), int(weight.group(1))), url.group(1))
    return files


def download_google(family):
    """Fetch a Google Font's regular, bold and italic files. Returns the cached files or None."""
    cached = cached_google_files(family)
    if cached or not DOWNLOADS_ENABLED or family not in GOOGLE_FONTS:
        return cached
    base = f"https://fonts.googleapis.com/css2?family={quote(family).replace('%20', '+')}"
    files = {}
    for axis in (":ital,wght@0,400;0,700;1,400;1,700", ":wght@400;700", ""):
        try:
            files = _css_files(_fetch(base + axis).decode("utf-8", "replace"))
        except (urllib.error.URLError, OSError, ValueError):
            continue
        if files:
            break
    if not files:
        return None
    folder = _google_dir(family)
    folder.mkdir(parents=True, exist_ok=True)
    wanted = {"regular": ("normal", 400), "bold": ("normal", 700), "italic": ("italic", 400), "bolditalic": ("italic", 700)}
    for style, key in wanted.items():
        url = files.get(key) or (files.get(("normal", 400)) if style == "regular" else None)
        if not url:
            continue
        try:
            data = _fetch(url, 3_000_000)
        except (urllib.error.URLError, OSError):
            continue
        if data[:4] in (b"\x00\x01\x00\x00", b"true", b"OTTO"):  # a real font file, not an error page
            (folder / f"{style}.ttf").write_bytes(data)
    return cached_google_files(family)


# --- font list, status and look-alikes -----------------------------------------------------------------------

def installed_fonts():
    import pdf_render
    return pdf_render.available_fonts()


def font_choices(extra=()):
    """(installed fonts, Google Fonts) for the pickers; extras (fonts found in the résumé) join the first list."""
    installed = installed_fonts()
    lead = [f for f in dict.fromkeys(e for e in extra if e) if f not in installed and f not in GOOGLE_FONTS]
    return lead + installed, sorted(GOOGLE_FONTS)


def similar_fonts(name, limit=4):
    key = (name or "").strip().lower()
    if key in LOOK_ALIKES:
        return LOOK_ALIKES[key][:limit]
    for word, fonts in LOOK_ALIKES.items():
        if word in key:
            return fonts[:limit]
    kind = GOOGLE_FONTS.get(name)
    if kind is None:
        kind = "mono" if "mono" in key or "courier" in key else "script" if re.search(r"script|hand|brush", key) else \
            "condensed" if re.search(r"condensed|narrow", key) else font_kind(name or "")
    same = [f for f, k in GOOGLE_FONTS.items() if k == kind and f != name]
    return (FALLBACK_BY_KIND.get(kind) or same)[:limit]


def font_status(name):
    """{'name', 'status': installed | google | missing, 'text', 'similar'} for one font name."""
    name = (name or "").strip()
    if not name:
        return {"name": "", "status": "same", "text": "Same as the body font", "similar": []}
    if name in installed_fonts():
        return {"name": name, "status": "installed", "text": "Installed on this computer", "similar": []}
    if name in GOOGLE_FONTS:
        ready = cached_google_files(name) is not None
        return {"name": name, "status": "google", "similar": [],
                "text": "Google Font, saved on this computer" if ready else "Google Font, downloaded the first time it is used"}
    return {"name": name, "status": "missing", "text": "Not found on this computer or in Google Fonts",
            "similar": similar_fonts(name)}


# --- the portfolio site -----------------------------------------------------------------------------------------

class _SameSiteRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlparse(newurl)
        if target.scheme != "https" or (target.hostname or "") not in PORTFOLIO_HOSTS:
            raise urllib.error.URLError(f"The site redirected somewhere else ({target.hostname}); not followed.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _site_get(url, limit=1_500_000):
    if (urlparse(url).hostname or "") not in PORTFOLIO_HOSTS:
        raise urllib.error.URLError("Only the portfolio site is read.")
    opener = urllib.request.build_opener(_SameSiteRedirects)
    with opener.open(urllib.request.Request(url, headers={"User-Agent": "ResumeBuilder/1.0"}), timeout=10) as response:
        return response.geturl(), response.read(limit + 1)[:limit].decode("utf-8", "replace")


def _first_font(declaration):
    first = declaration.split(",")[0].strip().strip("'\"")
    return first if first and first.lower() != "inherit" else ""


def read_fonts_from_css(css):
    """Role -> first font family for h1, h2, h3 and the article text, plus every family the site imports."""
    def family(selector_pattern):
        for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            if re.search(selector_pattern, selectors):
                found = re.search(r"font-family:\s*([^;}]+)", body)
                if found and _first_font(found.group(1)):
                    return _first_font(found.group(1))
        return ""
    roles = {label: family(rf"(^|[,\s;]){tag}(\s*,|\s*$)") for label, tag in PORTFOLIO_ROLES}
    roles["Body text"] = family(r"\.main-column p") or family(r"(^|[,\s])p(\s*,|\s*$)") or family(r"(^|[,\s])body(\s*,|\s*$)")
    imported = re.findall(r"family=([A-Za-z0-9+]+)", css)
    return {k: v for k, v in roles.items() if v}, list(dict.fromkeys(f.replace("+", " ") for f in imported))


def check_portfolio():
    """Read jamiekerig.com's page and stylesheet again. Returns the result and saves it with the design."""
    final_url, html = _site_get(PORTFOLIO_SITE)
    sheets = re.findall(r"<link[^>]+rel=[\"']stylesheet[\"'][^>]*>", html)
    css = ""
    for tag in sheets[:4]:
        href = re.search(r"href=[\"']([^\"']+)[\"']", tag)
        if href:
            css += _site_get(urljoin(final_url, href.group(1)))[1] + "\n"
    roles, imported = read_fonts_from_css(css)
    if not roles:
        raise ValueError("The site's stylesheet did not name any fonts.")
    result = {"checked_at": datetime.now().strftime("%Y-%m-%d"), "live": True, "roles": roles,
              "accents": [f for f in imported if f not in roles.values()], "colors": PORTFOLIO_READ["colors"]}
    state = load()
    state["portfolio"] = result
    _write(state)
    return result


def portfolio():
    return load().get("portfolio") or PORTFOLIO_READ


def portfolio_preset():
    """The résumé settings for matching the portfolio, using what the last read of the site found."""
    preset = dict(PORTFOLIO_PRESET)
    roles = portfolio()["roles"]
    for label, key in (("Titles (h1, project names)", "name_font"), ("Headings (h2)", "heading_font"),
                       ("Sub-headings (h3, h4)", "detail_font"), ("Body text", "font_family")):
        if roles.get(label):
            preset[key] = roles[label]
    preset["font_kind"] = font_kind(preset["font_family"])
    return preset


# --- the saved design ----------------------------------------------------------------------------------------------

def _file():
    return config.DATA_DIR / "design.json"


def load():
    try:
        state = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    return {"overrides": state.get("overrides") or {}, "notes": state.get("notes") or "",
            "portfolio": state.get("portfolio")}


def _write(state):
    config.ensure_dirs()
    _file().write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def save(overrides, notes):
    state = load()
    state["overrides"] = overrides
    state["notes"] = notes
    _write(state)


def reset():
    state = load()
    state["overrides"] = {}
    _write(state)


def overrides():
    return load()["overrides"]

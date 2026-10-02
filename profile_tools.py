"""Local resume suggestions and conservative, explainable skill matching."""
import json
import re
from pathlib import Path
from bs4 import BeautifulSoup

SKILL_ALIASES = {
    "HTML": ["html", "html5"], "CSS": ["css", "css3"], "Sass": ["sass", "scss"],
    "JavaScript": ["javascript", "ecmascript"], "TypeScript": ["typescript"],
    "React": ["react", "reactjs", "react.js"], "Vue": ["vue", "vue.js"],
    "Angular": ["angular"], "Python": ["python"], "PHP": ["php"],
    "SQL": ["sql"], "MySQL": ["mysql"], "Node.js": ["node.js", "nodejs"],
    "WordPress": ["wordpress"], "Drupal": ["drupal"], "Bootstrap": ["bootstrap"],
    "Git": ["git", "github", "gitlab"], "Docker": ["docker"],
    "Figma": ["figma"], "Adobe Photoshop": ["photoshop"],
    "Adobe Illustrator": ["illustrator"], "Adobe InDesign": ["indesign"],
    "Adobe Creative Cloud": ["adobe creative cloud", "adobe creative suite"],
    "Responsive Design": ["responsive design", "responsive websites", "responsive web design"],
    "Accessibility": ["accessibility", "wcag", "a11y"],
    "SEO": ["seo", "search engine optimization"],
    "Google Analytics": ["google analytics", "ga4"],
    "Google Tag Manager": ["google tag manager", "gtm"],
    "Hotjar": ["hotjar"], "BrowserStack": ["browserstack"], "Litmus": ["litmus"],
    "Salesforce Marketing Cloud": ["salesforce marketing cloud", "exacttarget"],
    "Amazon S3": ["amazon s3", "aws s3"],
    "Jira": ["jira"], "Confluence": ["confluence"], "Notion": ["notion"],
    "Trello": ["trello"], "monday.com": ["monday.com"],
    "jQuery": ["jquery"], "REST APIs": ["rest api", "restful api"],
    "Email Marketing": ["email marketing", "email campaigns"],
    "Content Management": ["content management", "cms"],
    "UI Design": ["ui design", "user interface design"],
    "UX Design": ["ux design", "user experience design"],
    "Project Management": ["project management", "project-managed"],
    "Canva": ["canva"], "Adobe After Effects": ["adobe after effects", "after effects"],
    "Microsoft Excel": ["microsoft excel", "excel spreadsheets"],
    # Design, web, email and marketing skills (all distinctive: none is an everyday word on its own).
    "Web Design": ["web design", "website design", "web designer"],
    "Web Development": ["web development", "web developer", "front end development", "frontend development", "front-end development",
                        "front end developer", "frontend developer", "front-end developer"],
    "Graphic Design": ["graphic design", "graphic designer"],
    "Visual Design": ["visual design"],
    "Landing Pages": ["landing page", "landing pages"],
    "A/B Testing": ["a/b testing", "a/b test", "ab testing", "split testing"],
    "SMS Marketing": ["sms marketing", "text message marketing"],
    "Digital Marketing": ["digital marketing"],
    "Marketing Automation": ["marketing automation"],
    "Social Media": ["social media"],
    "Copywriting": ["copywriting"],
    "Content Strategy": ["content strategy"],
    "Branding": ["branding", "brand identity"],
    "Typography": ["typography"],
    "Print Design": ["print design", "print production", "prepress", "pre-press"],
    "Wireframing": ["wireframe", "wireframes", "wireframing"],
    "Prototyping": ["prototype", "prototypes", "prototyping"],
    "Photo Editing": ["photo editing", "image editing", "photo manipulation", "photo retouching"],
    "Photography": ["photography"],
    "Video Editing": ["video editing", "video production"],
    "Adobe Premiere Pro": ["premiere pro", "adobe premiere"],
    "Adobe XD": ["adobe xd"],
    "Adobe Lightroom": ["lightroom"],
    "Adobe Acrobat": ["adobe acrobat"],
    "Microsoft Word": ["microsoft word", "ms word"],
    "Microsoft PowerPoint": ["powerpoint"],
    "Microsoft Office": ["microsoft office", "ms office", "office 365", "microsoft 365"],
    "Webflow": ["webflow"], "Squarespace": ["squarespace"], "Wix": ["wix"], "Shopify": ["shopify"],
    "WooCommerce": ["woocommerce"],
    "HubSpot": ["hubspot"], "Mailchimp": ["mailchimp"], "Marketo": ["marketo"], "Klaviyo": ["klaviyo"],
    "Constant Contact": ["constant contact"],
    "Google Ads": ["google ads", "adwords"],
    "CRM": ["crm"],
    "Cross-Browser Testing": ["cross-browser", "cross browser"],
    "QA Testing": ["qa testing", "quality assurance"],
    "Agile": ["agile", "scrum"],
    "Tailwind CSS": ["tailwind", "tailwind css"],
    "Webpack": ["webpack"],
    "Next.js": ["next.js", "nextjs"],
    "GraphQL": ["graphql"],
    "JSON": ["json"],
    "AWS": ["aws", "amazon web services"],
    "DNS": ["dns"],
    "VS Code": ["vs code", "visual studio code"],
    "Data Analysis": ["data analysis", "data analytics"],
}


# Skills that are also everyday words ("react quickly", "a notion of", "bootstrap the brand").
AMBIGUOUS_SKILLS = {"React", "Angular", "Notion", "Confluence", "Bootstrap", "Litmus"}
_TECH_AFTER = re.compile(r"^\s*(?:[,;/|)•·]|\.(?:\s|$)|\s+(?:and|or|&)\s|\s+(?:Native|Developer|Engineer|framework|library|JS|Hooks|Router|CSS)\b|$)")
_TECH_BEFORE = re.compile(r"(?:experience (?:with|in)|proficien\w+ (?:in|with)|knowledge of|familiar\w* with|expertise in|"
                          r"skills?:|using|such as|including|e\.g\.,?)\s*(?:[\w.+#-]+[,/]\s*)*$", re.I)


def _named_as_skill(text, skill):
    """An everyday-word skill counts only when capitalized and used like a tool (a list, 'experience with')."""
    for match in re.finditer(r"(?<![\w])" + re.escape(skill) + r"(?![\w])", text):
        if _TECH_AFTER.match(text[match.end(): match.end() + 40]) or _TECH_BEFORE.search(text[max(0, match.start() - 60): match.start()]):
            return True
    return False


def detect_skills(text):
    text = text[:250000]
    found = []
    for skill, aliases in SKILL_ALIASES.items():
        for alias in aliases:
            if skill in AMBIGUOUS_SKILLS and alias == skill.casefold():
                matched = _named_as_skill(text, skill)
            else:
                matched = re.search(r"(?<![\w])" + re.escape(alias) + r"(?![\w])", text, re.I)
            if matched:
                found.append(skill)
                break
    return found


def skill_demand(skill_lists, saved_skills, limit=12):
    """[(skill, how many listings name it)] for skills the profile lacks, most asked-for first.

    skill_lists are the stored skills of each listing Job Finder found (JSON text or lists)."""
    saved = {str(skill).casefold() for skill in saved_skills or []}
    counts = {}
    for raw in skill_lists:
        try:
            skills = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        except ValueError:
            continue
        for skill in normalize_skills(skills if isinstance(skills, list) else []):
            if skill.casefold() not in saved:
                counts[skill] = counts.get(skill, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold()))
    return ranked[:limit]


def uploaded_resume(folder):
    """(file name, text) of the résumé uploaded in the Resume Builder, or ("", "") when there isn't one. The Resume
    Builder saves the text it read from the upload next to the file (data/current-resume/text.txt); this only reads."""
    folder = Path(folder)
    try:
        text = (folder / "text.txt").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", ""
    try:
        name = str(json.loads((folder / "info.json").read_text(encoding="utf-8")).get("original_name") or "")
    except (OSError, ValueError, AttributeError):
        name = ""
    return (name[:120] or "your résumé"), text


def resume_skill_suggestions(folder, saved_skills):
    """(file name, skills named in the uploaded résumé that the profile doesn't list yet)."""
    name, text = uploaded_resume(folder)
    if not text.strip():
        return "", []
    saved = {str(skill).casefold() for skill in saved_skills or []}
    return name, [skill for skill in detect_skills(text) if skill.casefold() not in saved]


def normalize_skills(values):
    by_alias = {alias.casefold(): name for name, aliases in SKILL_ALIASES.items() for alias in [name, *aliases]}
    result = []
    for value in values:
        raw = str(value).strip()[:80]
        if not raw or not re.search(r"[a-zA-Z]", raw):
            continue
        canonical = by_alias.get(raw.casefold(), raw)
        if canonical.casefold() not in {item.casefold() for item in result}:
            result.append(canonical)
    return result[:100]


def listing_skills(html):
    soup = BeautifulSoup(html or "", "html.parser")
    for element in soup(["script", "style", "nav", "footer", "header"]):
        element.decompose()
    return detect_skills(soup.get_text(" ", strip=True))


def fit_score(user_skills, job_skills):
    user = {s.casefold() for s in user_skills}
    job = normalize_skills(job_skills)
    if not user:
        return {"score": None, "reason": "Add your skills on the Dashboard to see Job Fit.", "matched": [], "missing": []}
    if not job:
        return {"score": None, "reason": "The listing has too little skill information to calculate Job Fit.", "matched": [], "missing": []}
    matched = [s for s in job if s.casefold() in user]
    missing = [s for s in job if s.casefold() not in user]
    return {"score": round(100 * len(matched) / len(job)), "reason": "Skills found on the listing page; review the job requirements before applying.", "matched": matched, "missing": missing}


_MONTHS = {name: number for number, names in enumerate(
    [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"), ("jul", "july"),
     ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"), ("dec", "december")], 1)
    for name in names}
_MONTH_NAMES = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DATE_RANGE = re.compile(
    rf"^(?:(?P<m1>{_MONTH_NAMES})\.?\s+)?(?P<y1>(?:19|20)\d{{2}})\s*[-–—]\s*"
    rf"(?:(?P<m2>{_MONTH_NAMES})\.?\s+(?P<y2>(?:19|20)\d{{2}})|(?P<y3>(?:19|20)\d{{2}})|(?P<now>present|current|now))$", re.I)
_EXPERIENCE_HEADING = re.compile(r"^(?:(?:work|employment|professional|relevant|career)\s+)?(?:history|experience)$|"
                                 r"^(?:work|employment|professional)\s+(?:history|experience)$", re.I)
_OTHER_HEADING = re.compile(r"^(?:education(?:\s+history)?|skills|technical skills|core competencies|certifications?|"
                            r"additional experience|volunteer(?:ing)?(?: experience)?|projects|awards|references|"
                            r"professional summary|summary|objective|interests)$", re.I)
_PLACE_LINE = re.compile(r"^(?:remote|hybrid|on[- ]?site|[A-Za-z .'-]+,\s*[A-Z]{2})$", re.I)
_BULLET = re.compile(r"^[●•▪■◦‣\-*]$")


def _clean_lines(text):
    text = re.sub(r"[​‌‍﻿]", "", text).replace("\xa0", " ")
    return [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]


def _month_dates(line):
    """"January 2025 - May 2025" -> "2025-01 – 2025-05" (the form's month format); years only stay as years."""
    match = _DATE_RANGE.match(line.strip())
    if not match:
        return None
    start_month = _MONTHS.get((match.group("m1") or "").lower())
    start = f"{match.group('y1')}-{start_month:02d}" if start_month else match.group("y1")
    if match.group("now"):
        end = "Present"
    elif match.group("y2"):
        end = f"{match.group('y2')}-{_MONTHS[match.group('m2').lower()]:02d}"
    else:
        end = match.group("y3")
    return f"{start} – {end}"


def parse_work_history(text, limit=12):
    """Jobs listed under the résumé's experience heading: [{"role", "company", "dates", "description"}].

    Reads the common layout: a title line, a company line, an optional place line ("Remote", "Baltimore, MD"), a date
    range, then bullet points. Anything it cannot place is left out rather than guessed; the entries are suggestions
    for the person to check."""
    lines = _clean_lines(text)
    start = next((i for i, line in enumerate(lines) if _EXPERIENCE_HEADING.match(line)), None)
    if start is None:
        return []
    end = next((i for i in range(start + 1, len(lines)) if _OTHER_HEADING.match(lines[i])), len(lines))
    section = lines[start + 1:end]
    dated = [(i, _month_dates(line)) for i, line in enumerate(section) if _month_dates(line)]

    def header_start(at, floor):
        """Index where the job's heading (title, company, place) begins: up to three short, unpunctuated lines
        directly above its dates (single blank lines between them are fine), stopping at the previous job's text."""
        top, found = at, 0
        for index in range(at - 1, floor, -1):
            line = section[index]
            if not line:
                continue
            if _BULLET.match(line) or line.endswith(".") or len(line) > 70:
                break
            top, found = index, found + 1
            if found == 3:
                break
        return top

    starts = []
    floor = -1
    for at, _ in dated:
        starts.append(header_start(at, floor))
        floor = at
    entries = []
    for n, (at, dates) in enumerate(dated):
        names = [line for line in section[starts[n]:at] if line and not _PLACE_LINE.match(line)]
        if not names:
            continue
        stop = starts[n + 1] if n + 1 < len(dated) else len(section)
        bullets, current = [], ""
        for line in section[at + 1:stop]:
            if _BULLET.match(line):
                if current:
                    bullets.append(current)
                current = ""
            elif line:
                current = f"{current} {line}".strip()
        if current:
            bullets.append(current)
        entries.append({"role": names[0][:150], "company": names[1][:150] if len(names) > 1 else "", "dates": dates,
                        "description": "\n".join(f"• {item}" for item in bullets)[:3000]})
        if len(entries) >= limit:
            break
    return entries


def resume_suggestions(text):
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    candidates = lines[:12]
    name = next((line for line in candidates if re.fullmatch(r"[A-Za-z][A-Za-z' .-]{2,79}", line)
                 and 2 <= len(line.split()) <= 4 and not re.search(r"resume|curriculum|profile|experience", line, re.I)), "")
    location = next((match.group(0) for line in lines[:35]
                     if (match := re.search(r"\b[A-Za-z][A-Za-z .'-]{1,45},\s*(?:[A-Z]{2}|Maryland|Delaware|Virginia|Pennsylvania|New York|California)\b", line))), "")
    history = parse_work_history(text)
    # Work experience extraction is deliberately a suggestion: arbitrary resume layouts need human review.
    month = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    date_pattern = re.compile(rf"\b(?:{month}\s+)?(?:19|20)\d{{2}}\s*[-–—]\s*(?:{month}\s+)?(?:present|current|(?:19|20)\d{{2}})\b", re.I)
    for i, line in enumerate([] if history else lines):
        if date_pattern.search(line):
            preceding = lines[max(0, i-4):i]
            preceding = [s for s in preceding if len(s) < 110 and not re.match(r"^(?:remote|hybrid|on[- ]?site|[A-Za-z .'-]+,\s*[A-Z]{2})$", s, re.I)]
            if len(preceding) >= 2:
                history.append({"role": preceding[-2], "company": preceding[-1], "dates": line[:100]})
        if len(history) >= 12:
            break
    parts = name.split()
    return {"first_name": parts[0] if parts else "", "last_name": " ".join(parts[1:]) if parts else "",
            "home_location": location, "skills": detect_skills(text), "work_history": history}

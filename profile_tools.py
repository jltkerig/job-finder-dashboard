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


def resume_suggestions(text):
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    candidates = lines[:12]
    name = next((line for line in candidates if re.fullmatch(r"[A-Za-z][A-Za-z' .-]{2,79}", line)
                 and 2 <= len(line.split()) <= 4 and not re.search(r"resume|curriculum|profile|experience", line, re.I)), "")
    location = next((match.group(0) for line in lines[:35]
                     if (match := re.search(r"\b[A-Za-z][A-Za-z .'-]{1,45},\s*(?:[A-Z]{2}|Maryland|Delaware|Virginia|Pennsylvania|New York|California)\b", line))), "")
    # Work experience extraction is deliberately a suggestion: arbitrary resume layouts need human review.
    history = []
    month = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    date_pattern = re.compile(rf"\b(?:{month}\s+)?(?:19|20)\d{{2}}\s*[-–—]\s*(?:{month}\s+)?(?:present|current|(?:19|20)\d{{2}})\b", re.I)
    for i, line in enumerate(lines):
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

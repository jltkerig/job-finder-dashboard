"""Jobs and skills found in the user's reference documents that their Profile doesn't have yet.

Only suggestions: nothing is saved until the user ticks what to add on the Résumé Builder page.
"""
import re

import json

import config
import documents
import profile_import

DISMISSED_FILE = "dismissed-suggestions.json"
KINDS = ("job", "skill", "detail", "reference")


def dismissed():
    """{kind: set of keys} the user said no to; they are never suggested again."""
    path = config.DATA_DIR / DISMISSED_FILE
    try:
        saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except ValueError:
        saved = {}
    return {kind: set(saved.get(kind) or []) for kind in KINDS}


def dismiss(kind, key):
    if kind not in KINDS or not key:
        raise ValueError("Unknown suggestion.")
    current = dismissed()
    current[kind].add(key)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = config.DATA_DIR / DISMISSED_FILE
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({k: sorted(v) for k, v in current.items()}, indent=2), encoding="utf-8")
    temp.replace(path)


def job_key(job):
    return f"{job.get('role', '')}|{job.get('company', '')}".casefold()


def person_key(person):
    return person.get("name", "").casefold()

MONTH = profile_import.MONTH
YEAR = profile_import.YEAR
WHEN = rf"(?:{MONTH}\s+{YEAR}|\d{{1,2}}/{YEAR}|{YEAR})"
DATES = re.compile(rf"^{WHEN}\s*(?:[-–—]|to)\s*(?:{WHEN}|present|current|now)$", re.I)
ADDRESS = re.compile(r"^\d+\s+\S")  # "1 Main St, Springfield, MD"
NOT_A_TITLE = re.compile(r"^[\d\s()+.-]*$|@|https?://|^\d{5}(?:-\d{4})?$")
CONTACT = re.compile(r"phone:|e-?mail:|@|^\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}$", re.I)
STOP_WORDS = {"inc", "llc", "the", "of", "and", "co", "corp", "company", "ltd"}


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").casefold()) if len(w) > 1 and w not in STOP_WORDS}


def _is_title(line):
    return bool(line) and not NOT_A_TITLE.search(line) and not DATES.match(line) and not ADDRESS.match(line) \
        and len(line) <= 80 and not line.endswith(".") and any(c.isalpha() for c in line)


def jobs_in(text):
    """Jobs written as: title, dates, company (one or two lines), optional address, then what was done."""
    lines = profile_import._clean_lines(text)
    anchors = [i for i, line in enumerate(lines) if DATES.match(line) and i > 0 and _is_title(lines[i - 1])]
    jobs = []
    for n, i in enumerate(anchors):
        end = anchors[n + 1] - 1 if n + 1 < len(anchors) else len(lines)
        # a job ends at the next section heading ("Education History", "References"...), not just the next job
        end = next((k for k in range(i + 1, end) if profile_import.HEADINGS.match(lines[k])), end)
        body = lines[i + 1:end]
        company_lines, rest = [], body
        for k, line in enumerate(body):
            if ADDRESS.match(line):
                # an address ending in a comma carries on to the next line ("Baltimore," / "MD")
                rest = body[k + 2:] if line.endswith(",") else body[k + 1:]
                while rest and (profile_import.PLACE.match(rest[0]) or re.fullmatch(r"[A-Z]{2}(?:\s+\d{5})?", rest[0])):
                    rest = rest[1:]  # "Timonium, MD" left over from the address
                break
            if len(company_lines) == 2 or line.endswith(".") or len(line) > 60:
                rest = body[k:]
                break
            company_lines.append(line)
        else:
            rest = []
        company = " ".join(company_lines).strip()
        if not company or CONTACT.search(company) or NOT_A_TITLE.search(company):
            continue
        description = " ".join(line for line in rest if not CONTACT.search(line) and not profile_import.HEADINGS.match(line))
        jobs.append({"role": lines[i - 1][:150], "company": company[:150], "dates": lines[i][:100],
                     "description": description[:3000]})
    return jobs


def _known(job, history):
    role, company = job["role"].casefold(), _words(job["company"])
    for w in history:
        same_role = w["role"].casefold() == role
        same_company = bool(company & _words(w["company"]))
        if same_role and (same_company or not company):
            return True
    return False


def suggestions(profile):
    """{"jobs": [...], "skills": [...]}: what the documents add, each job tagged with the document it came from."""
    history = list(profile.get("work_history") or [])
    have_skills = {s.casefold() for s in profile.get("skills") or []}
    jobs, skills = [], []
    for record in documents.load():
        try:
            text = documents.text(record["id"])
        except (OSError, KeyError):
            continue
        for job in jobs_in(text):
            if not _known(job, history) and not _known(job, jobs):
                jobs.append({**job, "source": record["label"]})
        for skill in profile_import._detect_skills(text):
            if skill.casefold() not in have_skills and skill not in skills:
                skills.append(skill)
    no = dismissed()
    jobs = [j for j in jobs if job_key(j) not in no["job"]]
    skills = [s for s in skills if s.casefold() not in no["skill"]]
    for n, job in enumerate(jobs):
        job["key"] = str(n)
        job["dismiss"] = job_key(job)
    return {"jobs": jobs, "skills": skills}


# ---- Contact lists: job addresses, phones, supervisors and other people (possible references) ----

EMAIL = re.compile(r"^[\w.+-]+@([\w-]+(?:\.[\w-]+)+)$")
PHONE = re.compile(r"^\+?1?[\s.-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}$")
URL = re.compile(r"^(?:https?://)?(?:www\.)?([\w-]+(?:\.[\w-]+)+)/?\S*$", re.I)
ZIP = re.compile(r"^\d{5}(?:-\d{4})?$")
STATE = re.compile(r"^[A-Z]{2}$")
PERSON = re.compile(r"^[A-Z][a-z'’.-]+(?:\s+[A-Z][a-z'’.-]*\.?)?(?:\s+[A-Z][a-z'’-]+){1,2}$")
NOT_A_PERSON = re.compile(r"\b(?:manager|director|senior|junior|lead|chief|head|marketing|technology|design|designer|research|"
                          r"productions?|services|solutions|group|partners|media|studio|agency|company|corp|inc|llc|ltd|"
                          r"university|college|school|high|elementary|middle|academy|church|hall|county|city|hospital|center|bank|market|markets|foods?|web|digital|operations|"
                          r"president|officer|engineer|developer|specialist|coordinator|assistant|associate|analyst|ceo|cfo|cto)\b", re.I)


def _is_person(line):
    line = line.split(",")[0].strip() if line.count(",") == 1 else line  # a name may share its line with a title
    return bool(PERSON.match(line)) and not NOT_A_PERSON.search(line) and not profile_import.HEADINGS.match(line) \
        and not re.search(r"\b(?:history|experience|education|skills|references|summary|objective|awards|projects)\b", line, re.I)


def _domain(text):
    match = EMAIL.match(text or "") or URL.match(text or "")
    return (match.group(1) if match else "").lower().removeprefix("www.")


def contacts_in(text):
    """{"jobs": [{company, street, city, state, zip, phone, website, dates, supervisor...}], "people": [{name, title,
    phone, email, company}]} from a contact list laid out like: company, street, city, state, ZIP, phone, dates,
    supervisor name, title, email; and further down, people as name then phone, title and email in any order."""
    lines = profile_import._clean_lines(text)
    used, jobs = set(), []
    for i, line in enumerate(lines):
        if not DATES.match(line):
            continue
        job, k = {"dates": line}, i - 1
        for key, test in (("phone", PHONE.match), ("zip", ZIP.match), ("state", STATE.match)):
            if k >= 0 and test(lines[k]):
                job[key] = lines[k]
                used.add(k)
                k -= 1
        if k >= 0 and "zip" in job and not ADDRESS.match(lines[k]) and not _domain(lines[k]):
            job["city"] = lines[k]
            used.add(k)
            k -= 1
        if k >= 0 and ADDRESS.match(lines[k]):
            job["street"] = lines[k]
            used.add(k)
            k -= 1
        if k >= 0 and not _domain(lines[k]) and not EMAIL.match(lines[k]) and not DATES.match(lines[k]):
            job["company"] = lines[k]
            used.add(k)
            k -= 1
        if k >= 0 and URL.match(lines[k]) and "." in lines[k]:
            job["website"] = lines[k]
            used.add(k)
        if not job.get("company") or not (job.get("street") or job.get("phone")):
            continue
        n = i + 1
        if n < len(lines) and _is_person(lines[n]):
            job["supervisor_name"] = lines[n]
            used.add(n)
            n += 1
            if n < len(lines) and not EMAIL.match(lines[n]) and not PHONE.match(lines[n]) and not DATES.match(lines[n]):
                job["supervisor_title"] = lines[n]
                used.add(n)
                n += 1
            if n < len(lines) and EMAIL.match(lines[n]):
                job["supervisor_email"] = lines[n]
                used.add(n)
        used.add(i)
        jobs.append(job)
    people = []
    for job in jobs:
        if job.get("supervisor_name"):
            people.append({"name": job["supervisor_name"], "title": job.get("supervisor_title", ""),
                           "email": job.get("supervisor_email", ""), "phone": "", "company": job["company"], "note": ""})
    i = 0
    while i < len(lines):
        if i in used or not _is_person(lines[i]):
            i += 1
            continue
        name, _, title = lines[i].partition(",")  # "Kimberly Russell, Office Clerk"
        person, n = {"name": name.strip(), "title": title.strip(), "phone": "", "email": "", "company": "", "note": ""}, i + 1
        while n < len(lines) and n not in used and not _is_person(lines[n]) and not DATES.match(lines[n]) \
                and not profile_import.HEADINGS.match(lines[n]):
            if EMAIL.match(lines[n]):
                person["email"] = person["email"] or lines[n]
            elif PHONE.match(lines[n]):
                person["phone"] = person["phone"] or lines[n]
            elif len(lines[n]) > 80 or lines[n].endswith("."):
                person["note"] = f"{person['note']} {lines[n]}".strip()  # "Supervisor reference known for 3 year(s)."
            elif not person["title"]:
                person["title"] = lines[n]
            elif not person["company"]:
                person["company"] = lines[n]
            n += 1
        if person["email"] or person["phone"]:
            people.append(person)
        i = n
    # one entry per person, details merged; a missing company comes from their email's domain
    sites = {}
    for job in jobs:
        for key in ("website", "supervisor_email"):
            if _domain(job.get(key, "")):
                sites[_domain(job[key])] = job["company"]
    merged = {}
    for person in people:
        entry = merged.setdefault(person["name"].casefold(), {"name": person["name"], "title": "", "phone": "", "email": "", "company": "", "note": ""})
        for key, value in person.items():
            entry[key] = entry[key] or value
    for entry in merged.values():
        entry["company"] = entry["company"] or sites.get(_domain(entry["email"]), "")
    for job in jobs:
        person = merged.get(job.get("supervisor_name", "").casefold())
        if person and person["phone"] and person["phone"] != job.get("phone"):
            job["supervisor_phone"] = person["phone"]
    return {"jobs": jobs, "people": list(merged.values())}


def _same_company(a, b):
    first, second = _words(a), _words(b)
    return bool(first and second and (first <= second or second <= first or len(first & second) >= 2))


DETAIL_KEYS = ("street", "city", "state", "zip", "phone", "website", "supervisor_name", "supervisor_title", "supervisor_email",
               "supervisor_phone")
DETAIL_LABELS = {"street": "Street", "city": "City", "state": "State", "zip": "ZIP", "phone": "Phone", "website": "Website",
                 "supervisor_name": "Supervisor", "supervisor_title": "Supervisor title", "supervisor_email": "Supervisor email",
                 "supervisor_phone": "Supervisor's own phone"}


def detail_suggestions(profile):
    """For jobs already in the work history: details a contact list has that the job is still missing."""
    found = []
    for record in documents.load():
        try:
            text = documents.text(record["id"])
        except (OSError, KeyError):
            continue
        for contact in contacts_in(text)["jobs"]:
            for index, job in enumerate(profile.get("work_history") or []):
                if not _same_company(contact["company"], job.get("company", "")):
                    continue
                missing = {key: contact[key] for key in DETAIL_KEYS if contact.get(key) and not job.get(key)}
                if job_key(job) in dismissed()["detail"]:
                    continue
                if missing and not any(f["index"] == index for f in found):
                    found.append({"index": index, "role": job.get("role", ""), "company": job.get("company", ""),
                                  "details": missing, "source": record["label"], "dismiss": job_key(job),
                                  "supervisor": job.get("supervisor_name") or contact.get("supervisor_name", "")})
    return found


def job_index_for(person, profile):
    """The index of the user's job this person goes with: their "your job together", else their company; -1 if none."""
    history = profile.get("work_history") or []
    together = (person.get("user_job") or "").casefold()
    for index, job in enumerate(history):
        if together and together == " at ".join(p for p in (job.get("role", ""), job.get("company", "")) if p).casefold():
            return index
    for index, job in enumerate(history):
        if _same_company(person.get("company", ""), job.get("company", "")):
            return index
    return -1


def reference_suggestions(profile, saved_references):
    """People named in the documents who aren't saved as references yet, linked to the user's job with them."""
    saved = {r.get("name", "").casefold() for r in saved_references}
    supervising = {}
    for job in profile.get("work_history") or []:
        name = (job.get("supervisor_name") or "").casefold()
        if name:
            supervising.setdefault(name, []).append(" at ".join(p for p in (job.get("role", ""), job.get("company", "")) if p))
    # Off the list once ignored, or once they are both a supervisor and a reference.
    have = dismissed()["reference"] | {name for name in saved if name in supervising}
    have.add(f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip().casefold())  # never the user
    found = []
    for record in documents.load():
        try:
            text = documents.text(record["id"])
        except (OSError, KeyError):
            continue
        contacts = contacts_in(text)
        supervisors = {j.get("supervisor_name", "").casefold() for j in contacts["jobs"]}
        companies = [j["company"] for j in jobs_in(text) + contacts["jobs"] if j.get("company")]
        for person in contacts["people"]:
            if any(_same_company(person["name"], company) for company in companies):
                continue  # a company name that wrapped onto its own line, not a person
            if person["name"].casefold() in have or any(f["name"].casefold() == person["name"].casefold() for f in found):
                continue
            job = next((w for w in profile.get("work_history") or [] if _same_company(person["company"], w.get("company", ""))), None)
            found.append({**person, "relationship": "Supervisor" if person["name"].casefold() in supervisors
                          or re.search(r"\bsupervisor\b", person.get("note", ""), re.I) else "",
                          "user_job": " at ".join(p for p in ((job or {}).get("role", ""), (job or {}).get("company", "")) if p),
                          "company": (job or {}).get("company") or person["company"], "source": record["label"]})
    for n, person in enumerate(found):
        person["key"] = str(n)
        person["dismiss"] = person_key(person)
        person["job_index"] = job_index_for(person, profile)
        person["supervisor_on"] = supervising.get(person["name"].casefold(), [])
        person["is_reference"] = person["name"].casefold() in saved
    return found
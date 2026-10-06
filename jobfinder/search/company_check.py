"""Company-site check for jobs found on job sites and remote feeds: search for the same opening on the employer's own
site, read the page, and accept it only when the evidence adds up the way a person would judge it — the same title,
the same employer, the same place, the same posting text, and nothing saying the job is closed.

No AI calls: every signal is scored by rules. A match moves the listing's main link to the employer's page.
"""

from datetime import date, timedelta
import json
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from jobfinder.profiles.requirements import page_text
from jobfinder.search.company_site import is_directory_or_marketplace_result
from jobfinder.search.fetching import get_domain
from jobfinder.sources.posting_text import posting_text

CHECK_LIMIT = 15        # listings checked per run, so a search doesn't spend all its queries here
RECHECK_DAYS = 7        # a listing with no company posting found is tried again after this long
ACCEPT_SCORE = 60
NOT_CHECKED = "the company site was not checked"

# Job boards and aggregators: their copy is never the employer's own posting.
AGGREGATORS = {
    "linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com", "monster.com", "simplyhired.com", "careerbuilder.com",
    "dice.com", "jooble.org", "talent.com", "lensa.com", "jobright.ai", "usnlx.com", "nlx.org", "adzuna.com",
    "usajobs.gov", "builtin.com", "wellfound.com", "remotive.com", "weworkremotely.com", "remoteok.com", "himalayas.app",
    "jobgether.com", "learn4good.com", "salary.com", "snagajob.com", "getwork.com", "recruit.net", "jobilize.com",
    "theladders.com", "flexjobs.com", "workingnomads.com", "dailyremote.com", "jobleads.com", "careerjet.com",
    "whatjobs.com", "joblist.com", "freehire.me", "jobsora.com", "jobtoday.com", "hiring.cafe", "jobs.lever.co.uk", "teal.com", "tealhq.com", "bebee.com", "trabajo.org", "governmentjobs.com",
}
# Applicant systems host employers' own postings, usually under the employer's name.
ATS_HOSTS = ("myworkdayjobs.com", "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "smartrecruiters.com",
             "icims.com", "bamboohr.com", "recruitee.com", "teamtailor.com", "paylocity.com", "ultipro.com",
             "successfactors.com", "oraclecloud.com", "adp.com", "jobvite.com", "breezy.hr", "applytojob.com",
             "rippling.com", "paycomonline.net", "dayforcehcm.com", "taleo.net")
CLOSED = re.compile(r"no longer (?:accepting|available|open)|position has been filled|(?:job|posting|position) "
                    r"(?:has )?(?:expired|closed)|this (?:job|position|requisition) is (?:closed|no longer)", re.I)
COMPANY_FILLER = {"inc", "llc", "ltd", "corp", "corporation", "company", "co", "the", "group", "holdings", "and", "of",
                  "services", "solutions", "international", "usa", "us", "na", "plc", "lp", "llp", "pc"}
TITLE_FILLER = {"and", "or", "the", "a", "an", "of", "for", "to", "in", "at", "with", "remote", "hybrid", "onsite",
                "i", "ii", "iii", "iv", "full", "time", "part", "contract", "temporary", "job", "position",
                # seniority: "Senior Graphic Designer" vs "Senior Integrated Designer" share only these
                "senior", "sr", "junior", "jr", "lead", "principal", "staff", "associate", "mid", "level", "entry"}


def _words(text):
    return re.findall(r"[a-z0-9]+", str(text or "").casefold())


def _title_words(title):
    # Drop "(Remote)", "- Baltimore, MD" style tails before comparing.
    title = re.sub(r"\([^)]*\)|\s[-–|]\s.*$", " ", str(title or ""))
    return {word for word in _words(title) if word not in TITLE_FILLER}


def title_similarity(wanted, found):
    """Share of the wanted title's words found in the page title, and the other way round; the lower of the two, so
    "Designer" doesn't match "Senior Product Designer II, Payments"."""
    a, b = _title_words(wanted), _title_words(found)
    if not a or not b:
        return 0.0
    role = (re.findall(r"[a-z0-9]+", re.sub(r"\([^)]*\)|\s[-–|,]\s.*$|,.*$", " ", str(wanted).casefold())) or [""])[-1]
    if role and role not in b:
        return 0.0  # "Web Designer" is not "Web Developer", however much else matches
    shared = len(a & b)
    return min(shared / len(a), shared / len(b) + 0.25)


def company_words(company):
    return [word for word in _words(company) if word not in COMPANY_FILLER and len(word) > 1]


def _on_ats(host):
    return any(host == item or host.endswith("." + item) for item in ATS_HOSTS)


def company_in_url(company, url):
    """The employer's name in its own domain (acme.com, careers.acme.com), or in an applicant system's address
    (acme.wd5.myworkdayjobs.com, boards.greenhouse.io/acme). A job aggregator's path naming the company
    (freehire.me/jobs/designer-acme-123) doesn't count."""
    words = company_words(company)
    if not words:
        return False
    parts = urlparse(str(url or ""))
    host = (parts.hostname or "").casefold()
    where = host + (parts.path.split("/")[1] if _on_ats(host) and parts.path.count("/") >= 1 else "")
    squashed = re.sub(r"[^a-z0-9]", "", where)
    return "".join(words) in squashed or (len(words[0]) >= 4 and words[0] in squashed)


def _shingles(text, size=4):
    words = _words(text)[:4000]
    return {" ".join(words[i:i + size]) for i in range(max(0, len(words) - size + 1))}


def text_overlap(listing_text, page):
    """How much of the shorter text's 4-word phrases also appear in the other: 1.0 for the same posting."""
    a, b = _shingles(listing_text), _shingles(page)
    if len(a) < 20 or len(b) < 20:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _postings(soup):
    found = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (TypeError, ValueError):
            continue
        for item in data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                found.append(item)
    return found


def is_aggregator(domain):
    return any(domain == item or domain.endswith("." + item) for item in AGGREGATORS)


def judge(listing, url, html):
    """Score one page against the listing: {score, match, reasons}. A closed job, a different title or a different
    employer is never a match, whatever else agrees."""
    soup = BeautifulSoup(html or "", "html.parser")
    postings = _postings(soup)
    text = page_text(html)
    titles = [posting.get("title") for posting in postings]
    titles += [soup.title.get_text(" ", strip=True) if soup.title else ""]
    titles += [heading.get_text(" ", strip=True) for heading in soup.find_all("h1", limit=3)]
    similarity = max((title_similarity(listing["title"], title) for title in titles if title), default=0.0)
    reasons = []
    if similarity < 0.6:
        return {"score": 0, "match": False, "reasons": ["different job title"]}
    if CLOSED.search(text[:20000]):
        return {"score": 0, "match": False, "reasons": ["the posting says it is closed"]}
    score = round(similarity * 40)
    reasons.append(f"title matches ({similarity:.0%})")

    organisations = " ".join(str((posting.get("hiringOrganization") or {}).get("name") or "") for posting in postings
                             if isinstance(posting.get("hiringOrganization"), dict))
    words = company_words(listing["company"])
    if words and organisations and all(word in _words(organisations) for word in words):
        score += 25
        reasons.append("posted by the same employer")
    elif company_in_url(listing["company"], url):
        score += 20
        reasons.append("employer's name in the address")
    elif words and all(word in _words(text[:6000]) for word in words):
        score += 8
        reasons.append("employer named on the page")
    else:
        return {"score": 0, "match": False, "reasons": ["a different employer"]}

    place = [word for word in _words(listing.get("location")) if len(word) > 2 and word not in {"remote", "usa", "united", "states"}]
    if place and any(word in _words(text[:20000]) for word in place):
        score += 10
        reasons.append("same location")
    overlap = text_overlap(listing.get("description") or "", text)
    if overlap >= 0.3:
        score += 25
        reasons.append(f"same posting text ({overlap:.0%})")
    elif overlap >= 0.1:
        score += 10
        reasons.append(f"similar posting text ({overlap:.0%})")
    if postings:
        score += 10
        reasons.append("published as a job posting")
    return {"score": score, "match": score >= ACCEPT_SCORE, "reasons": reasons}


def candidate_urls(results, listing, limit=5):
    """Search results worth opening: not job boards or directories, on an applicant system or the employer's site."""
    found = []
    for result in results or []:
        url = result.get("url") or ""
        domain = get_domain(url)
        if not domain or url in found or is_aggregator(domain) or is_directory_or_marketplace_result(domain, result.get("title") or ""):
            continue
        on_ats = _on_ats(domain)
        if company_in_url(listing["company"], url) or (on_ats and title_similarity(listing["title"], result.get("title")) >= 0.6):
            found.append(url)
        if len(found) >= limit:
            break
    return found


def find_company_posting(listing, search, fetch):
    """The employer's own page for this listing, or None. search(query) gives [{url, title, content}] results; fetch(url) gives
    an object with .url and .text, or None."""
    company, title = listing["company"], listing["title"]
    if not company_words(company) or company.casefold() == "unknown employer":
        return None
    best = None
    for query in (f'"{title}" "{company}"', f"{company} careers {title}"):
        for url in candidate_urls(search(query), listing):
            page = fetch(url)
            if page is None:
                continue
            verdict = judge(listing, page.url or url, page.text)
            if verdict["match"] and (best is None or verdict["score"] > best["score"]):
                best = dict(verdict, url=page.url or url)
        if best:
            return best
    return None


def _due(details, today):
    checked = details.get("company_check")
    if checked:
        try:
            return date.fromisoformat(checked) <= today - timedelta(days=RECHECK_DAYS)
        except ValueError:
            return True
    return NOT_CHECKED in str(details.get("verification") or "")


def check_listings(database, search, fetch, limit=CHECK_LIMIT, today=None):
    """Look for the employer's own posting for job-site and feed listings that haven't been checked lately. Returns
    how many were found."""
    today = today or date.today()
    cursor = database.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id, name, career_job_title, career_url, source_url, source_type, career_credibility, "
                       "listing_details FROM companies WHERE is_rejected = 0 AND listing_details LIKE %s "
                       "AND COALESCE(job_open_status, '') <> 'Closed' ORDER BY date_found DESC",
                       ('%verification%',))
        rows = cursor.fetchall()
    finally:
        cursor.close()
    found = checked = 0
    for row in rows:
        try:
            details = json.loads(row.get("listing_details") or "{}") or {}
        except (TypeError, ValueError):
            continue
        if not _due(details, today):
            continue
        if checked >= limit:
            break
        checked += 1
        board_url = row.get("source_url") or row.get("career_url")
        description = details.get("description") or ""
        if not description and board_url:
            description = posting_text(board_url)  # NLX, Workday, Oracle pages build themselves with JavaScript
        if not description and board_url:
            board_page = fetch(board_url)
            description = board_page.text if board_page else ""
        listing = {"company": row.get("name") or "", "title": row.get("career_job_title") or "",
                   "location": details.get("location") or "", "description": page_text(description)}
        print(f"Company-site check {checked}: {listing['title']} — {listing['company']}", flush=True)
        try:
            match = find_company_posting(listing, search, fetch)
        except Exception as error:  # one odd page shouldn't stop the rest
            print(f"Company-site check failed: {error}", flush=True)
            continue
        details["company_check"] = today.isoformat()
        source = row.get("source_type") or "a job site"
        if match:
            found += 1
            details["verification"] = f"Found on the company site ({', '.join(match['reasons'])}); also listed on {source}"
            details["company_site_url"] = match["url"]
            print(f"  Found the employer's posting: {match['url']}", flush=True)
            update = ("UPDATE companies SET career_url = %s, source_url = %s, career_credibility = GREATEST(COALESCE(career_credibility, 0), 8), "
                      "listing_details = %s WHERE id = %s", (match["url"], board_url, json.dumps(details), row["id"]))
        else:
            details["verification"] = f"Listed on {source}; no matching posting found on the company site"
            update = ("UPDATE companies SET listing_details = %s WHERE id = %s", (json.dumps(details), row["id"]))
        cursor = database.cursor()
        try:
            cursor.execute(*update)
            database.commit()
        finally:
            cursor.close()
    return found

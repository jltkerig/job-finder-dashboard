# Job Finder

Personal job discovery project using Python, MySQL, Flask, the Brave Search API, public remote-job feeds (Remote OK, Remotive, We Work Remotely and Hacker News "Who is hiring?"), USAJOBS and Adzuna (optional free API keys), the State of Maryland's JobAps portal, employer career pages on Greenhouse, Lever, Workday, Ashby, iCIMS and similar systems, local O*NET occupation data, OpenStreetMap Nominatim for geocoding and Photon for address suggestions. The footer on every page lists these. The version number is shown in the dashboard footer and set in `dashboard.py` (`APP_VERSION`).

## What Job Finder does

Job Finder searches employer career sites, job sites, feeds and the web for job listings, checks each result for career and United States location evidence, and saves the findings to MySQL. You review them on the Search page, keep the ones you like, queue the ones you will apply to, and track the rest on the Dashboard. A companion app, **Résumé Builder**, writes a tailored résumé and cover letter for a job. Results are suggestions to review: a credibility score does not guarantee a company, opening, or application is legitimate. Job Finder never submits an application for you.

## The pages

| Page | What it is for |
| --- | --- |
| **Search** | Start a search, then work through **Results**: Top 10 Picks, Filters (text, state, work location, listing status, **source**, credibility, work type, found, sort) and the **Listings** table. Every box folds up. |
| **Companies** | Businesses, offices, plants and schools near a place (OpenStreetMap), with a **Search** card, **Filters** (Source: employers from your searches or *My companies*; Type of business pills with counts), **My Companies** (employers from your résumé plus ones you add) and **Add a Company of Your Own**. |
| **Dashboard** | Your profile (collapsible sections), **Job Apply Stats**, the **Apply Queue** and **Saved Jobs** with filters, Expand/Collapse all, bulk delete and Recent Searches. |
| **Résumé Builder** | Port 5001; its own app. See `resume-builder/README.md`. |
| **Settings** | Rejected listings (restore them) and blocked company names and domains. |
| **Tuning** | Search speed, your Brave key, the daily search and queue, and other settings. |

## Highlights

- **Brave Search plus direct sources.** Add your Brave key on the **Tuning** page (saved in `user-data/settings.json`, never in the repo). Without a key, searches still check employer career sites, job sites and feeds but skip the web search. If Brave refuses (quota used up or a wrong key) the web search stops for that run and says why.
- **Remote feeds.** Remote OK, Remotive, We Work Remotely and Hacker News "Ask HN: Who is hiring?" (read from the free Hacker News search service; only remote openings open to U.S. applicants whose title matches yours). Turn one off in `settings.json` with `"remote_feeds": {"Hacker News": false}`.
- **Web Job Scraper extension.** A Firefox extension that saves jobs you look at on LinkedIn, the Maryland Workforce Exchange and USAJOBS, and sends them to Job Finder with Job Fit %, distance and a 6 a.m. drive-time estimate from your Home ZIP. Its own README is in the `web-job-scraper` project.
- **Top 10 Picks.** Ranks the results shown by skill fit (required skills weigh more than nice-to-haves), distance and credibility, then reads each top listing's requirements: degree, years, clearance, focus areas and your **Avoid in Job Postings** terms. It sits above the Filters, and each pick has Keep, Apply, Details, Refresh, Reject and Closed buttons.
- **Apply Queue (Dashboard).** Jobs you Apply to, or that the daily search queues, wait here. **Open** shows the posting, **Mark Applied** records it (with a date you can change in the job's *Your Application* box), **Remove** takes it off the queue and **Reject** rejects the job. **Add Jobs to the Queue** lets you tick saved jobs or paste a link to any posting: Job Finder reads it, saves it as a job and queues it. The box stays on the page when the queue is empty.
- **Daily search.** On Tuning, turn on *Search every day and queue the best jobs*. Job Finder reruns your last search once a day after the hour you set; Top 10 then queues strong picks (no hard gaps).
- **Job Apply Stats.** Applied in total (Job Finder jobs plus applications you made elsewhere), Talking With Recruiter, Interview, and Applied By Month. Jobs applied to before dates existed are counted as *No Date Saved*.
- **Company-site check.** Jobs from the National Labor Exchange and remote feeds are looked up on the employer's own site. A page counts only when the title, employer, location and posting text agree and it isn't closed; the listing's link then moves to the employer's page and its credibility rises. Employers' own hiring boards (including iCIMS) also say whether a job is still open.
- **Easy Apply.** LinkedIn's Easy Apply badge shows only when Apply really opens LinkedIn.
- **Clear Results** deletes listings you haven't saved, queued, applied to or rejected. Rejected ones stay hidden, are never deleted by the clean-up, and Clear Results won't run during a search.
- **Résumé Builder links.** Each saved job shows the résumé and cover letter made for it, with Download links.
- Also: a Job Posting preview in each listing's Details, an **Ignore** list under Search for Jobs (shared with the profile's Avoid list), **Keep Browsing** while another tab searches, scam and not-a-job flags, duplicate listings merged with "Also listed on", and the dashboard answering only requests addressed to this computer (127.0.0.1 or localhost).

### Typical workflow

1. On the **Dashboard**, save your preferred job titles and search locations. Add a home City, State and ZIP, and optionally skills, work history, education, a primary title and a cropped photo. Each profile section folds up.
2. On **Search**, choose titles and city radii or full states, then start a search. The progress panel reports the current result and the number passing validation. Filter the **Results** (including by **Source**) or run **Top 10 Picks**.
3. Review each result's employer, job title, domain, location, distance, **Career Credibility**, **USA Credibility**, **Job Fit** and View link. Remote, Hybrid and Onsite appear only when the page supplies evidence. Open Details for the posting, requirements and verification. Use Keep to save a job, or Apply to queue it.
4. On the **Dashboard**, work the Apply Queue and Saved Jobs: filter, follow source links, track application status and notes, or refresh saved listings. Recent Searches can be run again or their titles added to your saved job titles.
5. In **Résumé Builder**, upload your résumé and reference documents once. Then paste a job link under **Job Link Résumé Creation** (or use Apply on a job) to get a tailored résumé and cover letter to download.
6. In **Settings**, search rejected listings, restore them, and maintain blocked company names and domains.

### Profile, résumé and Job Fit

Skills can be added manually or suggested from a résumé. The Dashboard's résumé import reads a PDF or DOCX in memory and presents suggested name, home location, skills and work history for review; it never overwrites existing entries. Résumé layouts vary, so suggested work history may need correction, and text inside scanned image PDFs cannot be extracted without OCR. Approved profile data and a cropped photo are stored in your local MySQL database. The Résumé Builder edits the same profile.

Job Fit compares your saved skills against recognizable skills found on the listing page. The percentage is the share of *detected page skills* that match your profile skills; it is not a prediction of hiring success. Hover or focus the score for context and the matched and missing skills.

### How a search works

New web searches follow employer career links and supported public job boards (Lever, Greenhouse and other employer systems), then save distinct matching postings with their own titles and View links. A general service page, news article, student employment guide, or company careers navigation page does not count as an individual opening. JobPosting data supplies title, location, date, schedule, salary and remote status where published; Details show the evidence and original source. Related O*NET titles can broaden searches while the matched title is recorded.

The search aims for 10 distinct jobs with at least 30% Career Credibility. Lower scoring leads can be reviewed during a search, and the automatic filter hides them when the latest search reaches 10 qualifying results; choose **Show all scores** to see them again. Credibility scores display as percentages (the stored 0–10 score times ten); the **Credibility Scores** page explains every current rule and limitation and lists possible future evidence sources.

### Search and update controls

| Control | What it checks |
| --- | --- |
| Start Job Finder | New search using the selected titles and locations (web search needs a Brave key). |
| Top 10 Picks | Ranks the results shown and lists the 10 best, with their requirements and gaps. |
| Clear Results | Deletes results you haven't saved, queued, applied to or rejected. |
| Refresh Results | Rechecks the currently displayed Search results. |
| Update Existing Results | Rechecks all active stored listings directly without a new web search. |
| Refresh Saved Listings | Rechecks saved listings shown under the Dashboard filters, with progress. |
| Search for Latest Version | Looks for a newer versioned ZIP in the parent project folder or Downloads; the updater backs up the installation and preserves local files. |

Job Finder never submits job applications (the Apply queue only opens postings) and does not verify businesses through external registries.

## Start

```powershell
.\start.ps1
```

Dashboard: `http://127.0.0.1:5000`

`start.ps1` checks Python dependencies, starts MySQL when needed, starts the local Flask dashboard, verifies `/app-version`, and opens the browser. It reads the expected version from `dashboard.py`, so no launcher version edit is needed for future releases. The dashboard also starts and restarts the Résumé Builder (port 5001) itself.

## Updates

Use **Search for Latest Version** in the dashboard. When a newer `job-finder-dashboard-vX.Y.Z.zip` is found, the button changes to **Update to vX.Y.Z**. The updater backs up the current install, preserves local user files, verifies the new files, and restarts Job Finder.

Preserved local files are `.env` and everything in `user-data/`, `logs/` and `user-builds/` (settings, blocked domain and company lists, search records, logs, and your saved résumés). Backup folders are named like `Job Finder v1.1.43 - 2026-09-28 at 01-20-00` (the version installed before the update and the local date and time); only the newest five are kept.

To make a release: `python release.py --restart` bumps the version, runs the tests, builds the ZIP (the newest five are kept in `job-finder-zips`), re-tests the unpacked ZIP and restarts the dashboard when no search is running.

## Location filtering

Saved cities can use 5, 10, 15, 20, 30 or 50 mile radii, or a whole state. Nominatim geocoding is rate-limited and cached in MySQL so repeated locations do not require another lookup. Address suggestions on the Companies page and in the Résumé Builder come from Photon (OpenStreetMap).

## Main files

Start here if you are new to the code (`docs/CODE-MAP.md` lists where each feature lives; `docs/BUGS.md` records the bugs found and the checks to run before finishing a change):

```
dashboard.py            the web app: start it, open http://127.0.0.1:5000 (APP_VERSION lives here)
job_finder.py           the search: run by the dashboard, or by hand with --help
start.ps1 / update.ps1  launcher with startup checks / updater with backup and rollback
release.py              builds a release ZIP: bumps the version, runs the tests, packs, re-tests the ZIP

jobfinder/              the code
  paths.py              where every file lives (read this first); creates user-data/ and logs/
  db.py                 the one place that opens a database connection
  web/                  the pages and buttons, one module per kind of page (see the list in dashboard.py)
                        (top_picks_routes, auto_apply for the daily search and Apply Queue, link_jobs for
                        adding a job by link, companies_routes, clear_results)
  search/               the search itself: runner.py is the search, the others are its parts
                        (shared settings, session, searching (Brave), fetching, company_site, company_check,
                        usa_location, geo, relevance, run_feeds ...)
  sources/              where jobs come from: job sites, feeds (job_feeds, hacker_news, remote_ok), employer
                        career boards (employer_jobs.py, with adapters split into employer_common/enterprise/
                        boards/more/jobaps), board_status for "is this job still listed"
  profiles/             your profile's helpers: skills and Job Fit, O*NET titles, places, drive times
  records/              what Job Finder remembers: skipped pages, board health, imported captures, retention

templates/, static/     the web pages (HTML, CSS, JavaScript)
data/                   reference data shipped with Job Finder (O*NET, places, ZIP codes) and data/defaults/
user-data/              YOURS: settings.json, block lists, watched employers, search records (updates never touch it)
logs/                   dashboard and search logs
user-builds/            résumés and cover letters from Résumé Builder
resume-builder/         Résumé Builder (its own app on port 5001; see its README). In the repo but not in release ZIPs
tests/                  python -m unittest discover -s tests -p "test_*.py" -t tests
```

To change a setting or block list, edit the file in `user-data/` (or use the Tuning and Settings pages). The tests use a temporary folder, so they never touch `user-data/` or the database.

Keep `.env` private and do not commit it.

## Data sources and credits

O*NET database information has been selected, ranked, and displayed in a modified form. Source: [O*NET 31.0 Database](https://www.onetcenter.org/database.html), U.S. Department of Labor, Employment and Training Administration (USDOL/ETA), used under the [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) license. USDOL/ETA has not approved or tested these modifications.

Places and addresses are from OpenStreetMap contributors (Nominatim and Photon). Each feed's own listing URL stays the link and the provider is named on the listing, as the providers ask: Remote OK, Remotive, We Work Remotely and Hacker News.

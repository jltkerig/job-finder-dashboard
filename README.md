# Job Finder v1.1.65

Personal job discovery project using Python, MySQL, Flask, Docker, SearXNG, Remote OK, local O*NET occupation data, and OpenStreetMap Nominatim.

## What Job Finder does

Job Finder searches the web locally for employer career pages and job listings, checks each result for career and United States location evidence, and saves the findings to MySQL. It uses a local SearXNG instance in Docker for new searches. Results are suggestions to review: a credibility score does not guarantee a company, opening, or application is legitimate.

## v1.1.65 — individual openings

New web searches follow employer career links and supported public Lever and Greenhouse job boards, then save distinct matching postings with their own titles and View links. A general service page, news article, student employment guide, or company careers navigation page does not count as an individual opening. JobPosting data supplies title, location, date, schedule, salary, and remote status where published; details show the evidence and original discovery source. Related O*NET titles can broaden searches while the matched title is recorded. Remote OK remains a separate source.

The search aims for 10 distinct jobs with at least 30% Career Credibility. Lower scoring individual leads can be reviewed during a search, and the automatic filter hides them when the latest search reaches 10 qualifying results. Choose **Show all scores** to see them again. The filter also supports a minimum score and schedule. A collapsed list explains recent skipped pages and optional rejection reasons can help review poor matches. Results include direct salaries only when the source publishes them.

Searches reuse fetched HTML, check a small number of linked pages concurrently, and limit career page exploration per employer. New installations default to shorter request spacing; existing `settings.json` values are preserved during updates. Each page still has a timeout and a failed source is skipped without aborting the entire search. JavaScript-only employer boards and pages that block automated requests may remain inaccessible; a missing date, salary, or work arrangement stays unknown. No login was added in this release.

### Typical workflow

1. On the **Dashboard**, save your preferred job titles and search locations. Add a home City, State for your profile, and optionally add skills, work history, a primary title, and a cropped photo. You can upload a PDF or DOCX résumé and review detected profile details before adding them. Save Profile after reviewing changes.
2. On **Search**, choose titles and city radii or full states, then start a search. The progress panel reports the current result, elapsed time, and the number passing validation. The results table can be filtered by text, state, work arrangement, and listing status, or sorted by title, company, distance, or credibility.
3. Review each result's employer, job title, domain, location, distance, **Career Credibility**, **USA Credibility**, **Job Fit**, and View link. Remote, Hybrid, and Onsite appear only when the page supplies evidence; otherwise Type is blank. Open Details for source and verification dates. Use Keep to save a job.
4. On the **Dashboard**, filter saved jobs, follow source links, track application status and notes, or refresh the saved listings shown by your filters. Recent Searches can be run again or their titles added to your saved job titles.
5. In **Settings**, search rejected listings, restore them, and maintain blocked company names and domains. A rejected listing is hidden from search; blocking a name or domain also excludes future results. The lists show block source and date when known.

### Profile, résumé, and Job Fit

Skills can be added manually or suggested from a résumé. Résumé upload reads a PDF or DOCX in memory and presents suggested name, home location, skills, and work history for review; it never automatically overwrites existing entries. Suggested work history may need correction because résumé layouts vary. Text inside scanned image PDFs cannot be extracted without OCR. The uploaded file itself is not saved; approved profile data and a cropped photo are stored in your local MySQL database.

Job Fit compares your saved skills against recognizable skills found on the listing page. The percentage is the share of *detected page skills* that match your profile skills; it is not a prediction of hiring success. Hover or focus the score for context and matched skills. **?** means either you have no saved skills (add them on the Dashboard) or the page provides too little skill information to score. An employer-wide career page may contain skills unrelated to one opening, so always read the job description.

### Search and update controls

| Control | What it checks |
| --- | --- |
| Start Job Finder | New web search using the selected titles and locations. |
| Refresh Results | Rechecks the currently displayed Search results. |
| Update Existing Results | Rechecks all active stored listings directly without a new web search. |
| Refresh Saved Listings | Rechecks saved listings shown under the Dashboard filters, with progress. |
| Search for Latest Version | Looks for a newer versioned ZIP in the parent project folder or Downloads; the updater backs up the installation and preserves local files. |

Job Finder does not submit job applications or verify businesses through external registries. The Credibility Scores page explains the current career and USA scoring rules and separates possible future evidence sources from current checks.

## Start

```powershell
.\start.ps1
```

Dashboard: `http://127.0.0.1:5000`

`start.ps1` checks Python dependencies, starts MySQL when needed, starts the local Flask dashboard, verifies `/app-version`, and opens the browser.
It reads the expected version from `dashboard.py`, so no launcher version edit is needed for future releases.

## Updates

Use **Search for Latest Version** in the dashboard. When a newer `job-finder-vX.Y.Z.zip` is found, the button changes to **Update to vX.Y.Z**. The updater backs up the current install, preserves local user files, verifies the new files, and restarts Job Finder.

Preserved local files include `.env`, `settings.json`, blocked domain and company lists, and logs. New backup folders are named like `Job Finder v1.1.43 - 2026-09-28 at 01-20-00` (the version installed before the update and local date/time). Earlier backups keep their original names.

## Location filtering

Saved cities can use 10, 15, 20, 30, or 50 mile radii. Nominatim geocoding is rate-limited and cached in MySQL so repeated locations do not require another lookup.

## Main files

- `start.ps1` — launcher and startup diagnostics
- `update.ps1` — updater/rollback
- `dashboard.py` — Flask dashboard
- `job_finder.py` — scraper/search logic
- `settings.json` — local scraper settings
- `templates/` — dashboard pages
- `static/` — CSS and JavaScript
- `searxng/` — Docker/SearXNG runtime configuration

Keep `.env` private and do not commit it.

## v1.1.38 reliability cleanup

- Removed Flask's competing source-file self-restart watcher; `start.ps1`/`update.ps1` now own restarts.
- Hardened port-5000 shutdown against changing/stale PIDs from older builds.
- Removed duplicate/development-only release files.
- Added updater cleanup for obsolete files left by older versions.

## v1.1.42

Merged regular-chat mobile layout and controls. Refresh Search preserves existing results; Update Existing Results checks all active listings and shows an Updated timestamp when tracked fields change. Saved buttons say Saved immediately, and page reloads do not prompt to leave.

## v1.1.43

Title suggestions appear after a 0.6 second pause; suggestion chips have readable dark text on light backgrounds.

## v1.1.44

Settings collects rejected listings and blocked domains. The header and footer align with page content; saved and rejected cards use compact actions, and credibility labels include hover and keyboard focus explanations.

## v1.1.45

The update-found dialog uses an Update button that begins installation. Other dialogs still use OK.

## v1.1.46

If a newer ZIP appears after a prior one was selected, the update dialog shows the newer version and allows installing it. Errors reset the update search so it can be retried.

## v1.1.47

Improved Dashboard and Settings card alignment, 5-item rejected and 25-item blocked-domain pagination, score explanations and score recalculation, search failure recovery, fixed save-column width, popup-only progress, button hover feedback, and the LinkedIn footer link.

## v1.1.48

Refresh Results reloads the existing listings without starting a new search or requiring title and city input. Update Existing Results returns immediately when there are no rows. Search buttons use consistent typography; Dashboard and Settings listing cards have aligned metadata and actions. Settings adds partial-match search and 10-item pages for domains and companies, and a 5-item page for rejected listings. The separate blocked company name list applies to future searches. Score tips and action hover contrast have been adjusted.

## v1.1.49

Refresh Results shows a visible loading indicator and confirms after the current database listings reload, even when the list has not changed.

## v1.1.50

Update Existing Results checks known sites directly without starting Docker or SearXNG. The popup reports the current listing and elapsed time. Clicking any Career Credibility or USA Credibility value opens the score explanation.

## v1.1.51

Search results have a dedicated State filter. Two-letter abbreviations match states exactly, and full state names such as Alabama resolve to their abbreviation.

## v1.1.52

Search results use the requested column order: Save, Company, Job, Type, Domain, Career Credibility, Location, Distance, USA Credibility, Listing, Actions. Type is Remote, Hybrid, Onsite, or — when the job page is unclear; new searches and Update Existing Results fill this field, and the results toolbar can filter by type.

## v1.1.53

Refresh Results rechecks the listings currently displayed in the results table, showing the listing being checked and elapsed time; Update Existing Results continues to check every active listing. Type detection recognizes more explicit remote, hybrid, and onsite terms and stores them in `work_arrangement`. Listing buttons use two columns, Save stays stationary, and card titles use smaller condensed text. Career and USA Credibility replace the previous Confidence names throughout the interface and database; startup migrates existing score columns without resetting their values.

## v1.1.54

Fix the missing Type filter reference that stopped JavaScript setup and left Search for Latest Version and other page buttons unresponsive. Version the script URL so Firefox loads the updated script after installing this ZIP.

## v1.1.55

Search results use a narrower, stationary Save button and narrower centered score columns. The table header text is smaller, the listing link reads View, and the Actions column has enough room for two button columns. Narrow screens retain the result card layout.

## v1.1.56

Rejected and blocked search listings fade out and trigger a one-result replacement search using the latest saved search criteria. If no criteria are saved or a search is running, the page reports that. Existing blocked domains and companies show their source with unknown dates; new blocks save a timestamp and User source in `block_metadata.json`. The updater preserves that file. Rejected listing titles wrap, actions use a consistent row, Save is vertically centered, and the Location, Distance and USA Credibility columns are narrower.

## v1.1.57

Search and replacement search show a live result number, current title and elapsed time in the working popup. Replacement search shows its one-result target. The search process now writes progress without output buffering.

## v1.1.58

A full state name such as Maryland now creates a statewide location instead of a city-radius center. City entries still use their individual radii. The working popup shows separate live lines for the current result and the count of listings that passed validation, including those already stored.

## v1.1.59

An official .edu page with a U.S. campus address now supplies strong USA Credibility evidence (at least 8). The scraper skips student employment guidance pages from financial-aid sections when they do not contain a specific JobPosting. Update Existing Results recalculates existing scores and can mark such pages Closed. Previously saved directory-style UMBC student overview results are hidden from search.

## v1.1.60

Credibility values display as percentages (the existing 0–10 score multiplied by ten) without changing stored scores. The Credibility Scores page explains every current rule and limitation, and identifies potential external verification sources as future ideas.

## v1.1.61

Added a Dashboard skills and work history editor, résumé suggestions with explicit review, a home location and primary title, a cropped profile photo, Save Job Title on recent searches, and a filtered saved-listing refresh with progress. Search and saved jobs show a Job Fit estimate when both user skills and listing-page skills are available. This release also expands the README with a current feature overview.

## v1.1.64

- Match common title spelling variants such as Front End and Frontend in Remote OK listings.
- Show the feed's reported location on Remote OK results. Listings outside the U.S. no longer receive an unsupported 50% USA credibility score when USA-only filtering is off.
- Save Remote OK matches even if Docker or SearXNG cannot start. A vanished listing becomes Unknown on a successful feed refresh, and reappearing listings become Open.

## v1.1.62

- New searches also check the Remote OK public feed for recent remote positions matching the selected job titles. A few feed matches can be saved alongside the normal web search results. The View link goes to Remote OK as required by its feed terms. Feed listings are attributed on the result and in the footer. An API listing is a lead to review, not an independently verified employer career page; its career and USA credibility values reflect limited feed evidence.
- Bundled the August 2026 O*NET 31.0 Database occupation titles and skills files locally. Search title suggestions now use related occupation titles; Dashboard skill suggestions include software examples for the selected primary occupation. These are optional suggestions. Job Fit still compares saved user skills with skills actually detected on the listing, not all skills associated with an occupation. No O*NET Web Services registration or connection is used.
- O*NET database information has been selected, ranked, and displayed in a modified form. Source: [O*NET 31.0 Database](https://www.onetcenter.org/database.html), U.S. Department of Labor, Employment and Training Administration, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). USDOL/ETA has not approved, endorsed, or tested these modifications. The downloaded source files are in `data/onet-31.0/`; update them when a new release is available.

## v1.1.63

Fixed the Windows PowerShell launcher so an import error from a missing Python package reaches the automatic dependency installation step. It now shows the relevant Python error and reports a clear startup code if installation fails.

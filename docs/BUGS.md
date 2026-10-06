# Bugs found, and what to check for

A running list of bugs found in Job Finder, grouped by kind, so the same kinds can be checked for after every change.
Each entry: what went wrong, why, the fix, and the check that would have caught it. Newest first within a group.

## Before reporting any change as done

- [ ] Run the full test suite (`python release.py --restart` runs it) and read the result **before** committing.
- [ ] After a reader, matching or scoring change, audit **all** open listings, not just the example that was reported
      (read every posting like Top 10 does; list the unread and thin ones by site).
- [ ] After a search change, run a real search with 2–3 titles outside the usual ones and read the log: what passed,
      what was saved, where, and why it stopped. Back up the profile first; remove test rows and history after.
- [ ] Search new/edited source files for stray control characters (`grep -c $'\x08'`): shell heredocs turn `\b` into a
      backspace.
- [ ] Close test servers and headless browsers when done (a leftover PHP server on :8080 once blocked the search).

## Reading postings

- **Pages that build themselves with JavaScript read as menus only** (2026-10-06). National Labor Exchange, Workday and
  Oracle Cloud pages return a shell ("Skip To Main Content…"). Fix: `jobfinder/sources/posting_text.py` gets the text
  from each system's data service. Check: audit shows unread/thin listings grouped by host.
- **iCIMS postings sit in a frame** (2026-10-06). The page itself said only "Privacy Policy - Aptive Resources", and the
  listing still scored 99. Fix: read `?in_iframe=1` first. Check: same audit.
- **A page shell was trusted as the posting** (2026-10-06). Fix: text under 600 characters counts as unread, so the
  pick gets "Couldn't read the requirements". Check: no Details panel shows only a sentence or two.
- **Unread pages were fetched again on every Top 10** (2026-10-06), making it slow. Fix: remember the failure for the
  day (`unread_on_v<VERSION>`). Bump `requirements.VERSION` after any reader change so cached results are re-read.
- **Experience areas longer than four words were missed** (2026-10-06): "4–5 years of SQL database administration and
  programming experience". Fix: allow up to six words.

## Matching titles

- **Related titles widened to the bare role** (2026-10-06). O*NET suggestions for "Product Designer" included
  "Designer", "Fur Designer", "Toy Designer", so Textile, Computer-Aided, Instructional and engineering "Design" jobs
  matched. Fix: a related title must share a describing word with a typed title or be a digital/design-field title.
  Check: `expand_job_titles([...])` for any new title family never contains a one-word title.
- **Run-together O*NET titles** (2026-10-06): "UX Designer (User Experience Designer)" lost its brackets and became a
  suggestion "UX Designer User Experience Designer". Fix: skip suggestions over four words.
- **Two of three words matched without the role** (2026-10-06): "Analytics Engineering Manager, Data Platform &
  Governance" matched "Data Governance Analyst". Fix: the near-match rule in `matching_title` needs the last word
  (the role). Check: for a new title family, try titles that share the field words but a different role.
- **Not a bug: adjunct teaching jobs in the field match** ("Adjunct Faculty, Graphic Design" for Graphic Designer). The
  user wants these kept (2026-10-06); Top 10's teaching focus area marks them down a little instead.
- **Typos in saved titles silently find nothing** ("Production Specalist"). Not fixed in code; tell the user.

## Saving

- **One feed job saved three times** (2026-10-06). We Work Remotely posts the same job under addresses ending `-1`,
  `-2`. Fix: the feed loop skips a company + title it already took in this run. Check: after a search, group saved
  rows by company + title; anything over one from the same source is a duplicate.

## Location and work arrangement

- **"Not Remote" read as Remote** (2026-10-06). Workday's `remoteType: "Not Remote"` contains "remote", so San Francisco
  jobs passed a Baltimore 20-mile search as remote jobs. Fix: `_arrangement` checks for not/non/no remote first.
  Check: any field read with `"remote" in text` must handle negatives.
- **Workday page data says TELECOMMUTE for onsite and hybrid jobs** (2026-10-06): a New York job and a "Client
  Site" job were saved as Remote. Fix: for myworkdayjobs.com, Workday's own `remoteType` (Office / Hybrid / Remote /
  Not Remote) wins over the page's JobPosting data. Check: Remote rows from Workday should have remoteType Remote.
- **Known issue: "Washington" + "University of Maryland" reads as MD** (row 1). Refresh re-reads the page each time,
  so a hand fix to DC doesn't stick. Displayed distance is right (35.6 mi); only the state label is off.
- **Cities were not checked when added** (2026-10-06): "zzzzqq" or "Baltimre" were accepted and then searched as
  nowhere. Fix: Add looks the place up first (`/city-matches`).

## Company-site check

- **A third-party repost ranked #3 although the employer's site didn't have it** (2026-10-06, Charter Global via
  thecreativeloft.com; Charter Global's site warns about recruiter scams). The verification said "this job is not
  listed there" but Top 10 ignored it. Fix: a third-party board's copy that the employer's site doesn't list gets a
  warning and a 30-point penalty (LinkedIn employer posts excepted). Check: Top 10 picks whose link isn't the
  employer's domain should show where the job was verified.
- **Known issue: wrong employer-site guesses** ("domain guess"): Collins + Co. → rtx.com, Catalyst Mobility →
  calstart.org, Freedom Technology Solutions → freedom.com. The "not listed there" result is then meaningless.

- **Job aggregators counted as the employer's site** (2026-10-06). freehire.me addresses name the company in the path
  (`/jobs/…-lawnstarter-…`), so 8 listings' links were switched to an aggregator. Fix: the company name must be in the
  employer's own domain or an applicant system's address; freehire and others added to `AGGREGATORS`.
  Check: after a search, list `company_site_url` hosts; none should be a job board.
- **More aggregators with the company in the path** (2026-10-06): talents.vaia.com, designremotejobs.com. 19 listings
  in all had their link switched to an aggregator; restored. The domain rule covers these without listing each one.
- **Seniority words made different jobs match** (2026-10-06): "Senior Graphic Designer" matched "Senior Integrated
  Designer". Fix: senior/lead/staff/… are ignored when comparing titles.
- **A different role counted as the same job** (2026-10-06): "Web Designer, eCRM" matched a "Web Developer, eCRM" page.
  Fix: the typed title's role word must appear in the page's title.

## Search engine and environment

- **SearXNG couldn't start because port 8080 was taken** (2026-10-06) by a leftover PHP test server, so the web search
  was skipped. SearXNG was later removed; the search uses Brave only. Check: close test servers.
- **A search stopped right after starting** (2026-10-06) because Stop was pressed in another open Search tab. Not a
  code bug; keep in mind when a test search ends with "Stop requested before the web search started".

## Top 10

- **Closed LinkedIn jobs stayed in Top 10** (2026-10-06): Kee Group and 5 others said "No longer accepting
  applications" but were Open. LinkedIn jobs aren't rechecked by Refresh, and a description saved at capture never shows
  a later closing. Fix: posting text with closed wording marks the job Closed (Top 10 and Refresh); the best 12 picks
  get a live check once a day. Check: scan saved posting text with `company_check.CLOSED` for rows still Open.
- **After a refresh, picks already in the Apply queue showed "Apply" again** (2026-10-06). Fix: rows carry
  `data-queued`, and the button shows "Added to Apply".

- **First pick scored 99 with no readable posting** (2026-10-06): see "Reading postings".
- **Picks reloaded on every page refresh** (2026-10-06). Fix: the last Top 10 is kept in the browser for a day.

## Earlier bugs (before 2026-10-06)

### Data safety
- **A test overwrote the real profile** (2026-10-01). A Résumé Builder test mocked the read but not the save, and the
  résumé auto-fill wrote a fake "Jane Doe" profile over the real one. Fix: tests cut the database off at the
  connection (`tests/no_database.py`). Check: after adding anything that writes, run the tests and confirm real data is
  unchanged.
- **Saving the Dashboard profile overwrote changes made in Résumé Builder** since the page loaded (v1.1.163). Fix: the
  save checks the profile version first.
- **The updater deleted README.md** (v1.1.122). Check: the updater's obsolete-files list only names files that are
  really gone.
- **Personal data in a public repo** (2026-10-03): history was rewritten. Check: before any push, scan staged files for
  personal details; tests use made-up people only.

### Release and editing
- **Shell heredocs corrupted regexes** (several times): `\b` became a backspace character, `\b` became `b`. Check:
  `grep -c $'\x08' <file>` after scripted edits; prefer the Edit tool for regex lines.
- **A failed release was committed** (twice) because the commit was chained after the release with `;`. Check: read
  the release result before committing.
- **Error codes reused** (E3220 was taken). Check: grep for a code before using it.
- **Python edits switched files to CRLF**, so the version bump regex stopped matching. Check: keep each file's line
  endings (read and write with `newline=""`).
- **A stray launcher exe was committed** with `git add -A`. Check: look at `git status` before committing.
- **Restarting mid-search** cuts a search off. Check: `/search-status` before restarting (release.py does).

### Reading and judging listings
- **Pages decoded with the wrong encoding** showed `�` in titles. Fix: `decode_page` tries UTF-8, the declared encoding,
  then cp1252. (Some titles still show `�`: check new sources.)
- **"Washington, DC" wasn't recognised as DC.** Fix in `find_state_from_text`.
- **"degree in Baltimore"**: a place read as a degree field. Fix: degree fields that start with a place are skipped.
- **"agency" read as advertising work** on county job pages. Fix: advertising needs "ad agency"/"creative agency".
- **Writing flagged on a developer job** ("writing code", "writing tests"). Fix: those phrases are excluded.
- **Focus areas flagged from passing mentions** ("our brand" 3 times). Fix: an area needs 5+ mentions.
- **About 50 listings had no skills**, so their fit was unknown. Fix: skills are read from the posting text, including
  the user's own skills by name.
- **"0 of 1 required skill"** when the posting had no requirements list. Fix: shows "N of M listed skills (none
  required)".
- **"Unknown employer" although the link is the employer's own site** (2026-10-06, warschawski.com). Fix: Refresh
  falls back to the site's name (`name_from_url`), skipping job boards and applicant systems.
- **Company names with codes or taglines** ("1234 ACME", "Acme │ We build things"), and a name equal to the job title.
  Fix: `tidy_company_name`.
- **Marketing pages saved as jobs** ("WordPress web design in Gaithersburg"). Fix: `NOT_A_JOB` in job_listings.py and
  Top 10; a word-boundary bug in that regex was fixed after.
- **The same job saved from several sites** showed as separate rows. Fix: `merge_duplicates` keeps the employer's site
  and lists the others as "Also listed on".

### Ranking (Top 10)
- **A clearance-heavy or scammy listing ranked high** (Aegis; an "HR Plus" listing based in Hong Kong). Fix: clearance
  titles are a hard gap; worldwide/low-USA-credibility and bait titles get warnings.
- **Credibility outweighed skill fit**, so LinkedIn listings (credibility 3) sank. Fix: fit 50%, distance 25%,
  credibility 25%.
- **Noisy titles** ("X is hiring: Graphic Designer in Baltimore") counted as a title mismatch. Fix: `cleanTitle`.
- **Requirements read for only the top 20**, so a better job below never got its boost. Fix: read the top 40.
- **A footwear graphics job ranked in the top 10.** Fix: focus areas the profile never mentions cost points.

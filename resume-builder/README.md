# Résumé Builder

Writes a tailored résumé and cover letter for a job and gives you the PDFs to review, edit and download. The writing is done by Claude through a connector (Claude Desktop) or, for **Job Link Résumé Creation**, by Claude Code running on this computer, using your Claude subscription with no per-use charge. This app holds your uploaded résumé, your profile (shared with Job Finder), your reference documents and people, your writing rules and your résumé design.

## What is on the page

All of these fold up, and each remembers whether you left it open.

- **How to Use It**: the four steps, and whether Claude Desktop is connected.
- **Uploads**: **Your Current Résumé** (a PDF or Word file; its layout, fonts and sections are copied into everything written) and **Reference Documents** (past cover letters, reviews, certificates, project lists; Claude reads them in every chat and uses only what is in them).
- **Job Link Résumé Creation**: paste a link to a job and press **Create Résumé and Cover Letter**. The posting is read, Claude Code writes both files with only this app's tools, and Download buttons appear as they are saved. Nothing to copy or paste.
- **Résumé Design**: what the app sees in your résumé (margins, header, headings, fonts, spacing), editable fonts, sizes, margins, header and heading settings, and the design notes Claude reads. Presets such as *Match My Portfolio*.
- **Profile**: name, location, links, titles, skills, work history (with optional street, city, state, ZIP, phone, website and supervisor per job), and education. It writes to Job Finder's profile tables. Titles and skills get suggestions as you type, and street addresses get suggestions from OpenStreetMap. A résumé upload fills gaps only; links and jobs the résumé lists that the profile lacks are offered with a button, never added automatically.
- **People**: references (relationship, their job, contact details, private notes that are never printed, and whether they agreed to be a reference).
- **Résumé Rules** and **Cover Letter Rules**: your own standing instructions. Claude reads them first, and they win over the built-in rules.
- **Saved Résumés and Cover Letters**: search by job, company, file name or type; tick files and **Delete Selected** (a backup is taken first); Review & edit or Download each one.

## Start

```powershell
.\start.ps1        # starts the app on http://127.0.0.1:5001 (and opens it)
.\install-connector.ps1   # once: connects Claude Desktop (then quit and reopen Claude Desktop)
```

Job Finder's dashboard also starts and restarts this app itself. It reads your profile from Job Finder's database, so Job Finder's MySQL must be running. **Job Link Résumé Creation** needs Claude Code (`claude`) installed and signed in on this computer.

## Backups

Everything this app keeps (saved résumés and cover letters, drafts, reference documents, people, rules, design and the uploaded résumé) is copied into `resume-builder-backups/` next to the Job Finder folders as a ZIP: at start-up, every hour while running, and just before anything is deleted or replaced. A snapshot is skipped when nothing changed or when the files suddenly drop to under half of the last one (a `WARNING.txt` is left instead), so a wipe can never push the good copies out. The newest five are kept.

```powershell
python backup.py list                 # show the snapshots
python backup.py now                  # take one now
python backup.py restore <file.zip>   # put a snapshot's files back (replaces same-named files, deletes nothing)
```

## Project map

```
app.py             the web pages (Flask) and every button on them
mcp_server.py      the connector: the tools Claude calls (read résumé, read rules, save résumé, ...)
auto_writer.py     Job Link Résumé Creation: runs Claude Code with only the connector's tools, reports progress
job_link.py        reads a job posting from a pasted link (title, company, text)
backup.py          the snapshots described above, and restore
models.py          the shape of a résumé, a cover letter and the layout settings
pdf_render.py      draws the PDFs (fonts, margins, spacing come from the layout settings)
design.py          the font list (Windows + Google Fonts), look-alike suggestions, saved design
design_report.py   reads the uploaded résumé and writes out what it sees (margins, header, headings ...)
resume_file.py     saving the upload, reading its text, measuring its layout
builds.py          saving finished PDFs and their editable drafts
profile_import.py  filling your profile from the résumé (never without a click)
skill_suggest.py, address_suggest.py   suggestions while you edit the profile
jobfinder_db.py    reading and saving the profile in Job Finder's database
documents.py, references.py, connector.py, config.py   reference documents, references, connector status, paths
templates/, static/   the pages
data/              YOURS, not committed: uploaded résumé, drafts, references, rules, design.json, downloaded fonts
tests/             python -m unittest discover -s tests
```

Finished PDFs are saved in Job Finder's `user-builds/` folder. Résumé Builder lives inside Job Finder's folder and reads Job Finder's code from the folder above it (set `JOB_FINDER_DIR` to change it). The tests use temporary folders and stop at once if they are ever pointed at the real ones; keep every Résumé Builder test inside `tests/test_resume_builder.py`.

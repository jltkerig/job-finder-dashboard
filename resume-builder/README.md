# Résumé Builder

Writes a résumé and cover letter for a saved job. Claude Desktop does the writing through a connector; this app
holds your uploaded résumé, your profile (shared with Job Finder), your references and your résumé design, and shows
the finished PDFs for review, editing and download.

## Start

```powershell
.\start.ps1        # starts the app on http://127.0.0.1:5001 (and opens it)
.\install-connector.ps1   # once: connects Claude Desktop (then quit and reopen Claude Desktop)
```

It reads your profile from Job Finder's database, so Job Finder's MySQL must be running.

## Project map

```
app.py             the web pages (Flask) and every button on them
mcp_server.py      the Claude Desktop connector: the tools Claude calls (read résumé, save résumé, ...)
models.py          the shape of a résumé, a cover letter and the layout settings
pdf_render.py      draws the PDFs (fonts, margins, spacing come from the layout settings)
design.py          the font list (Windows + Google Fonts), look-alike suggestions, jamiekerig.com type, saved design
design_report.py   reads the uploaded résumé and writes out what it sees (margins, header, headings ...)
resume_file.py     saving the upload, reading its text, measuring its layout
builds.py          saving finished PDFs and their editable drafts
profile_import.py  filling your profile from the résumé (never without a click)
jobfinder_db.py    reading and saving the profile in Job Finder's database
documents.py, references.py, connector.py, config.py   reference documents, references, connector status, paths
templates/, static/   the pages
data/              YOURS, not committed: uploaded résumé, drafts, references, design.json, downloaded fonts
tests/             python -m unittest discover -s tests
```

Finished PDFs are saved in Job Finder's `user-builds/` folder. Résumé Builder lives inside Job Finder's folder and reads Job Finder's code from the folder
above it (set `JOB_FINDER_DIR` to change it).

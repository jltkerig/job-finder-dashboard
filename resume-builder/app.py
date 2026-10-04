"""Resume Builder web app: upload the current resume, review and edit what Claude saved, download PDFs."""
import re
import secrets
import sys
from io import BytesIO

from flask import (Flask, abort, flash, jsonify, redirect, render_template, request, send_file, send_from_directory,
                   session, url_for)

import builds
import config
import connector
import design
import document_import
import documents
import jobfinder_db
import profile_import
import references
import resume_file
import writing_rules
from pydantic import ValidationError

from models import (DEFAULT_LAYOUT, RELATIONSHIPS, LayoutSettings, PrintedReference, Reference, ResumeContent,
                    merge_layout)
from pdf_render import available_fonts, render_resume

config.ensure_dirs()
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_BYTES + 64 * 1024


def _secret_key():
    path = config.DATA_DIR / ".secret_key"
    if not path.exists():
        path.write_text(secrets.token_hex(32), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


app.secret_key = _secret_key()


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


@app.before_request
def refuse_foreign_hosts():
    """Answer only requests addressed to this computer, so a web page cannot use "DNS rebinding" to read the
    resume, references and notes from here."""
    host = request.host.split("]")[0] + "]" if request.host.startswith("[") else request.host.rsplit(":", 1)[0]
    if host.lower() not in LOCAL_HOSTS:
        abort(400)


@app.before_request
def check_csrf():
    expected = session.get("csrf")
    if request.method == "POST" and (not expected or not secrets.compare_digest(request.form.get("csrf", ""), expected)):
        abort(400, "This form expired. Go back, reload the page and try again.")


@app.context_processor
def template_globals():
    return {"csrf_token": csrf_token, "app_version": config.APP_VERSION, "job_finder_url": config.JOB_FINDER_URL,
            "form_examples": form_examples()}


def form_examples():
    """Your own examples for empty boxes, from Job Finder's user-data/form-examples.json; {} when there are none."""
    if str(config.JOB_FINDER_DIR) not in sys.path:
        sys.path.append(str(config.JOB_FINDER_DIR))
    try:
        from jobfinder import form_examples as examples
        return examples.examples()
    except Exception:  # Job Finder's code missing or broken: just no examples
        return {}


@app.get("/")
def index():
    profile, profile_error = None, ""
    try:
        profile = jobfinder_db.get_profile()
    except jobfinder_db.JobFinderUnavailable as error:
        profile_error = str(error)
    # Suggestions for "your job together": Job Finder work history, then jobs already typed.
    past_jobs = [" at ".join(p for p in (w["role"], w["company"]) if p) for w in (profile or {}).get("work_history", [])]
    past_jobs += [r["user_job"] for r in references.load() if r["user_job"]]
    # Jobs the uploaded résumé lists that the Profile doesn't have yet: offered, never added without a click.
    resume_jobs = []
    if profile and resume_file.current_info():
        try:
            resume_jobs = profile_import.new_jobs(resume_file.current_text(), profile)
        except Exception:  # an unreadable résumé just means no offer
            resume_jobs = []
    # The same for the reference documents, plus skills they name: offered with tick boxes, added only on request.
    document_finds = {"jobs": [], "skills": [], "details": [], "people": []}
    if profile:
        try:
            document_finds = {**document_import.suggestions(profile),
                              "details": document_import.detail_suggestions(profile),
                              "people": document_import.reference_suggestions(profile, references.load())}
        except Exception:  # an unreadable document just means no suggestions
            pass
    return render_template("index.html", profile=profile, profile_error=profile_error, resume_jobs=resume_jobs,
                           document_finds=document_finds, education_degrees=jobfinder_db.EDUCATION_DEGREES,
                           reference_jobs={r['id']: document_import.job_index_for(r, profile) for r in references.load()} if profile else {},
                           profile_version=jobfinder_db.profile_version(profile) if profile else "", detail_labels=document_import.DETAIL_LABELS,
                           **_design_context(),
                           resume=resume_file.current_info(), builds=builds.list_builds(),
                           connector=connector.status(), output_dir=config.OUTPUT_DIR,
                           references=references.load(), relationships=RELATIONSHIPS,
                           past_jobs=list(dict.fromkeys(j for j in past_jobs if j)), documents=documents.load(),
                           resume_rules=writing_rules.load("resume"), cover_letter_rules=writing_rules.load("cover_letter"))


DESIGN_FONT_ROLES = (("name_font", "Name Font"), ("heading_font", "Section Heading Font"),
                     ("font_family", "Body Text Font"), ("detail_font", "Dates, Sub-headings and Contact Font"))
FONT_NAME = re.compile(r"^[A-Za-z0-9 .&'\-]{0,60}$")
DESIGN_NUMBERS = ("body_size", "name_size", "heading_size", "line_spacing", "section_gap",
                  "margin_top", "margin_right", "margin_bottom", "margin_left")
DESIGN_CHOICES = ("page_size", "name_align", "heading_case", "bullet_char", "contact_separator", "accent_color", "text_color")


def _effective_layout():
    info = resume_file.current_info()
    return merge_layout((info or {}).get("layout"), design.overrides())


def _design_context():
    """Everything the Résumé Design section shows: what the résumé looks like, the settings and each font's status."""
    if not resume_file.current_info():
        return {"design_on": False}
    report = resume_file.current_design() or {"rows": [], "notes": "", "facts": None}
    layout = _effective_layout()
    unsaved = request.args.get("preset") == "portfolio"
    if unsaved:  # the form is filled with the portfolio's type; nothing is saved until Save Design
        layout = merge_layout(layout, design.portfolio_preset())
    seen = [x["font"] for x in (report["facts"] or {}).get("fonts", [])]
    installed, google = design.font_choices(extra=seen + [layout[key] for key, _ in DESIGN_FONT_ROLES])
    portfolio = design.portfolio()
    return {
        "design_on": True, "design_rows": report["rows"], "design_layout": layout, "design_unsaved": unsaved,
        "design_notes": design.load()["notes"] or report["notes"],
        "font_roles": [(key, label, design.font_status(layout[key])) for key, label in DESIGN_FONT_ROLES],
        "installed_fonts": installed, "google_fonts": google, "portfolio": portfolio,
        "portfolio_fonts": [(label, font, design.font_status(font)) for label, font in portfolio["roles"].items()],
    }


def _design_from_form():
    """The Résumé Design form as layout settings; raises ValueError with a message the person can act on."""
    form = request.form
    data = {}
    for key, _ in DESIGN_FONT_ROLES:
        value = form.get(key, "").strip()
        if not FONT_NAME.match(value):
            raise ValueError("font names can only use letters, numbers, spaces and . & ' -")
        data[key] = value
    if not data["font_family"]:
        raise ValueError("choose a body text font.")
    for key in DESIGN_NUMBERS:
        raw = form.get(key, "").strip()
        try:
            data[key] = float(raw) if raw else None
        except ValueError:
            raise ValueError(f"{key.replace('_', ' ')} must be a number.") from None
    for key in DESIGN_CHOICES:
        data[key] = form.get(key, "") or None
    data["heading_rule"] = form.get("heading_rule") == "on"
    data["header_rule"] = form.get("header_rule") == "on"
    data["font_kind"] = design.font_kind(data["font_family"])
    try:
        return LayoutSettings(**data).model_dump(exclude_none=True)
    except ValidationError as error:
        first = error.errors()[0]
        raise ValueError(f"{str(first['loc'][0]).replace('_', ' ')}: {first['msg']}") from None


@app.post("/design/save")
def save_design():
    try:
        overrides = _design_from_form()
    except ValueError as error:
        flash(f"Design not saved: {error}", "error")
        return redirect(url_for("index") + "#design")
    notes = request.form.get("notes", "").replace("\r\n", "\n").strip()[:6000]
    design.save(overrides, notes)
    fetched, missing = [], []
    for name in dict.fromkeys(overrides.get(key) for key, _ in DESIGN_FONT_ROLES):
        status = design.font_status(name)["status"] if name else "same"
        if status == "google":  # saved now, so making a PDF never waits on the network
            (fetched if design.download_google(name) else missing).append(name)
        elif status == "missing":
            missing.append(name)
    message = "Saved your design. New résumés and cover letters use it."
    if fetched:
        message += f" Fonts saved from Google Fonts: {', '.join(fetched)}."
    if missing:
        message += f" Not found: {', '.join(missing)}. A similar built-in font is used until you pick one of the suggestions."
    flash(message, "warn" if missing else "ok")
    return redirect(url_for("index") + "#design")


@app.post("/design/reset")
def reset_design():
    design.reset()
    flash("Your design now matches your uploaded résumé again.", "ok")
    return redirect(url_for("index") + "#design")


@app.post("/design/reread")
def reread_design():
    info = resume_file.reanalyze_design()
    if info:
        design.save(design.overrides(), info["design"]["notes"])
        flash("Looked at your résumé again and wrote out its design.", "ok")
    return redirect(url_for("index") + "#design")


@app.post("/design/portfolio")
def check_portfolio():
    try:
        result = design.check_portfolio()
    except (OSError, ValueError) as error:
        flash(f"Couldn't read jamiekerig.com just now ({error}). The type read last time is still shown.", "error")
    else:
        flash("Read the type on jamiekerig.com: " + ", ".join(dict.fromkeys(result["roles"].values())) + ".", "ok")
    return redirect(url_for("index") + "#design")


@app.get("/design/preview.pdf")
def design_preview():
    """A made-up résumé drawn in the saved design, so fonts, margins and spacing can be seen at once."""
    content = ResumeContent.model_validate({
        "full_name": "Your Name Here", "headline": "Frontend Web Developer and Graphic Designer",
        "contact": ["(555) 555-0100", "you@example.com", "yourportfolio.com"],
        "sections": [
            {"title": "Professional Summary", "text": "Frontend developer with years of experience building responsive, accessible sites and campaigns."},
            {"title": "Employment History", "items": [
                {"heading": "Web Designer", "subheading": "Example Company", "location": "Remote", "dates": "January 2025 - May 2025",
                 "bullets": ["Rebuilt and migrated pages using Bootstrap.", "Improved layout and user experience across the site."]},
                {"heading": "Front End Web Developer", "subheading": "Sample Research LLC", "location": "Baltimore, MD",
                 "dates": "May 2015 - October 2024", "bullets": ["Designed responsive landing pages and emails."]}]},
            {"title": "Skills", "text": "HTML, CSS, JavaScript, Bootstrap, Figma"}]})
    out = BytesIO()
    render_resume(content, _effective_layout(), out)
    out.seek(0)
    return send_file(out, mimetype="application/pdf", max_age=0)


@app.post("/documents/add")
def add_document():
    file = request.files.get("document")
    if not file or not file.filename:
        flash("Choose a file first.", "error")
    else:
        try:
            record = documents.add(file.filename, file.read(), request.form.get("label", ""))
        except documents.DocumentError as error:
            flash(f"Not added: {error}", "error")
        else:
            note = "" if record["chars"] else " No text was found in it (a scan or image?); Claude will look at the page pictures instead."
            flash(f"Added {record['label']}.{note}", "ok")
    return redirect(url_for("index") + "#documents")


@app.post("/documents/<doc_id>/rename")
def rename_document(doc_id):
    try:
        documents.rename(doc_id, request.form.get("label", ""))
    except KeyError:
        abort(404)
    flash("Label saved.", "ok")
    return redirect(url_for("index") + "#documents")


@app.post("/documents/<doc_id>/delete")
def delete_document(doc_id):
    try:
        documents.delete(doc_id)
    except KeyError:
        abort(404)
    flash("Document deleted.", "ok")
    return redirect(url_for("index") + "#documents")


@app.get("/documents/<doc_id>/file")
def document_file(doc_id):
    try:
        path, row = documents.file_path(doc_id)
    except KeyError:
        abort(404)
    # Text files are sent as downloads so a .txt/.md can never be shown as a web page.
    inline = row["type"] == "PDF"
    return send_file(path, as_attachment=not inline, download_name=row["original_name"], max_age=0)


def _reference_form():
    return {field: request.form.get(field, "") for field in Reference.model_fields if field != "id"}


@app.post("/references/add")
def add_reference():
    try:
        reference = references.add(_reference_form())
    except ValueError as error:
        flash(f"Not added: {error}", "error")
    else:
        flash(f"Added {reference['name'] or 'the reference'}.", "ok")
    return redirect(url_for("index") + "#references")


@app.post("/references/<ref_id>/update")
def update_reference(ref_id):
    try:
        reference = references.update(ref_id, _reference_form())
    except KeyError:
        abort(404)
    except ValueError as error:
        flash(f"Not saved: {error}", "error")
    else:
        flash(f"Saved {reference['name'] or 'the reference'}.", "ok")
    return redirect(url_for("index") + "#references")


@app.post("/references/<ref_id>/delete")
def delete_reference(ref_id):
    try:
        references.delete(ref_id)
    except KeyError:
        abort(404)
    flash("Reference deleted.", "ok")
    return redirect(url_for("index") + "#references")


@app.post("/profile/add-from-documents")
def add_from_documents():
    """Adds only the jobs and skills the user ticked from the reference-document suggestions."""
    try:
        profile = jobfinder_db.get_profile()
    except jobfinder_db.JobFinderUnavailable as error:
        flash(f"Not saved: {error}", "error")
        return redirect(url_for("index") + "#profile")
    found = document_import.suggestions(profile)
    picked_jobs = [j for j in found["jobs"] if j["key"] in set(request.form.getlist("job"))]
    picked_skills = [s for s in found["skills"] if s in set(request.form.getlist("skill"))]
    picked_details = [d for d in document_import.detail_suggestions(profile) if str(d["index"]) in set(request.form.getlist("detail"))]
    picked_people = [p for p in document_import.reference_suggestions(profile, references.load())
                     if p["key"] in set(request.form.getlist("reference"))]
    if not (picked_jobs or picked_skills or picked_details or picked_people):
        flash("Tick at least one item to add.", "error")
        return redirect(url_for("index") + "#document-suggestions")
    parts = []
    if picked_jobs or picked_skills or picked_details:
        if not (profile["first_name"] and profile["last_name"]):
            flash("Save your first and last name in the Profile first.", "error")
            return redirect(url_for("index") + "#profile")
        history = [dict(w) for w in profile["work_history"]]
        for item in picked_details:  # only fills what the job is missing
            history[item["index"]].update(item["details"])
        jobs = [{k: j[k] for k in ("role", "company", "dates", "description")} for j in picked_jobs]
        history = sorted(history + jobs, key=_job_start, reverse=True)
        primary = profile["primary_job_title"]
        jobfinder_db.save_profile(profile["first_name"], profile["last_name"], profile["home_location"], primary,
                                  [t for t in profile["job_titles"] if t != primary], profile["skills"] + picked_skills, history)
        parts += [_count(len(jobs), "job"), _count(len(picked_skills), "skill"), _count(len(picked_details), "job's details", "jobs' details")]
    for person in picked_people:
        references.add({"relationship": person["relationship"], "name": person["name"], "job_title": person["title"],
                        "company": person["company"], "user_job": person["user_job"], "phone": person["phone"],
                        "email": person["email"]})
    parts.append(_count(len(picked_people), "reference"))
    flash(f"Added {', '.join(p for p in parts if p)}.", "ok")
    return redirect(url_for("index") + "#profile")


@app.post("/profile/set-supervisor")
def set_supervisor():
    """Fill one job's supervisor name, title and email from a person: a suggested one, or a saved reference."""
    source, _, key = request.form.get("person", "").partition(":")
    try:
        profile = jobfinder_db.get_profile()
    except jobfinder_db.JobFinderUnavailable as error:
        flash(f"Not saved: {error}", "error")
        return redirect(url_for("index") + "#profile")
    if source == "suggestion":
        person = next((p for p in document_import.reference_suggestions(profile, references.load()) if p["key"] == key), None)
        title = (person or {}).get("title", "")
        anchor = "#found-people"
    elif source == "reference":
        person = next((r for r in references.load() if r["id"] == key), None)
        title = (person or {}).get("job_title", "")
        anchor = "#references"
    else:
        abort(400)
    try:
        index = int(request.form.get(f"sup_job_{key}", ""))
    except ValueError:
        index = -1
    if person is None or not 0 <= index < len(profile["work_history"]):
        flash("Choose which job they supervised.", "error")
        return redirect(url_for("index") + anchor)
    if not (profile["first_name"] and profile["last_name"]):
        flash("Save your first and last name in the Profile first.", "error")
        return redirect(url_for("index") + "#profile")
    history = [dict(w) for w in profile["work_history"]]
    job = history[index]
    job.update({"supervisor_name": person["name"], "supervisor_title": title or job.get("supervisor_title", ""),
                "supervisor_email": person.get("email") or job.get("supervisor_email", ""),
                "supervisor_phone": person.get("phone") or job.get("supervisor_phone", "")})
    primary = profile["primary_job_title"]
    jobfinder_db.save_profile(profile["first_name"], profile["last_name"], profile["home_location"], primary,
                              [t for t in profile["job_titles"] if t != primary], profile["skills"], history)
    flash(f"{person['name']} is now the supervisor on {job['role']} at {job['company']}.", "ok")
    return redirect(url_for("index") + anchor)


@app.post("/suggestions/dismiss")
def dismiss_suggestion():
    """One "No thanks" button on the suggestions: that job, skill, set of job details or person isn't offered again."""
    kind, _, key = request.form.get("dismiss", "").partition(":")
    try:
        document_import.dismiss(kind, key)
    except ValueError:
        abort(400)
    return redirect(url_for("index") + ("#found-people" if kind == "reference" else "#document-suggestions"))


@app.post("/references/add-suggested")
def add_suggested_reference():
    """Add one person found in the reference documents to References, with what the documents say about them."""
    try:
        profile = jobfinder_db.get_profile()
    except jobfinder_db.JobFinderUnavailable:
        profile = {}
    person = next((p for p in document_import.reference_suggestions(profile, references.load())
                   if p["key"] == request.form.get("key")), None)
    if person is None:
        flash("That person is no longer suggested.", "error")
        return redirect(url_for("index") + "#found-people")
    references.add({"relationship": person["relationship"], "name": person["name"], "job_title": person["title"],
                    "company": person["company"], "user_job": person["user_job"], "phone": person["phone"],
                    "email": person["email"]})
    flash(f"Added {person['name']} to your References.", "ok")
    return redirect(url_for("index") + "#found-people")


def _count(n, one, many=None):
    return "" if not n else f"{n} {one if n == 1 else (many or one + 's')}"


def _job_start(job):
    """A sortable (year, month) from a job's dates, so added jobs land in order; undated jobs go last."""
    match = re.search(r"(?:(\d{1,2})/)?((?:19|20)\d{2})", job.get("dates") or "")
    if not match:
        return (0, 0)
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    named = (job.get("dates") or "").strip()[:3].lower()
    month = int(match.group(1) or 0) or (months.index(named) + 1 if named in months else 0)
    return (int(match.group(2)), month)


@app.post("/writing-rules/<kind>")
def save_writing_rules(kind):
    if kind not in writing_rules.KINDS:
        abort(404)
    anchor = "#resume-rules" if kind == "resume" else "#cover-letter-rules"
    try:
        writing_rules.save(kind, request.form.get("rules", ""))
    except ValueError as error:
        flash(f"Not saved: {error}", "error")
    else:
        flash(f"{writing_rules.TITLES[kind]} saved.", "ok")
    return redirect(url_for("index") + anchor)


def _onet():
    """Job Finder's O*NET job-title data (Resume Builder has no copy of its own)."""
    if str(config.JOB_FINDER_DIR) not in sys.path:
        sys.path.append(str(config.JOB_FINDER_DIR))
    try:
        from jobfinder.profiles import onet_data
    except ImportError:
        return None
    return onet_data


@app.get("/job-title-matches")
def job_title_matches():
    onet = _onet()
    return jsonify({"matches": onet.title_matches((request.args.get("q") or "")[:100]) if onet else []})


@app.get("/job-title-suggestions")
def job_title_suggestions():
    onet = _onet()
    titles = [t.strip() for t in (request.args.get("titles") or "")[:1000].split(",") if t.strip()]
    have = {t.casefold() for t in titles}
    found = []
    for title in titles:
        for suggestion in (onet.related_title_suggestions(title) if onet else []):
            if suggestion.casefold() not in have and suggestion not in found:
                found.append(suggestion)
    return jsonify({"suggestions": found[:8]})


@app.post("/profile/fill-from-resume")
def fill_profile_from_resume():
    """Add the uploaded résumé's jobs (and anything else the Profile lacks) to the Job Finder profile."""
    try:
        added = profile_import.fill_profile(resume_file.current_text())
    except jobfinder_db.JobFinderUnavailable as error:
        flash(f"Your Profile wasn't filled in: {error}", "error")
    else:
        flash("Filled in your Profile from the résumé: " + ", ".join(added) + ". Check it below." if added else
              "Nothing new to add: your Profile already has what the résumé lists.", "ok")
    return redirect(url_for("index") + "#profile")


@app.post("/profile")
def save_profile():
    sent_version = request.form.get("profile_version", "")
    try:
        current = jobfinder_db.profile_version(jobfinder_db.get_profile())
    except jobfinder_db.JobFinderUnavailable as error:
        flash(str(error), "error")
        return redirect(url_for("index") + "#profile")
    if sent_version and sent_version != current:
        flash("Your profile was changed in Job Finder after this page loaded, so nothing was saved here. "
              "The page now shows the latest version: make your change again, then Save.", "error")
        return redirect(url_for("index") + "#profile")
    first = request.form.get("first_name", "").strip()
    last = request.form.get("last_name", "").strip()
    if not first or not last:
        flash("Enter both a first and last name.", "error")
        return redirect(url_for("index") + "#profile")

    def split(name):
        return [part.strip() for part in request.form.get(name, "").replace("\n", ",").split(",") if part.strip()]

    history = [{field: request.form.get(f"wh{i}_{field}", "") for field in ("role", "company", "dates", "description") + jobfinder_db.WORK_DETAILS}
               for i in range(min(int(request.form.get("wh_count", 0) or 0), 50))]
    onet = _onet()
    proper = onet.proper_title if onet and hasattr(onet, "proper_title") else (lambda text: text)  # titles get proper capitalization
    try:
        education = [{field: request.form.get(f"ed{i}_{field}", "") for field in jobfinder_db.EDUCATION_SIZES}
                     for i in range(min(int(request.form.get("ed_count", 0) or 0), 20))]
        for i, school in enumerate(education):  # month boxes give YYYY-MM; "Currently attending" means the end is Present
            if request.form.get(f"ed{i}_current"):
                school["end_date"] = "Present"
        jobfinder_db.save_profile(first, last, request.form.get("home_location", ""),
                                  proper(request.form.get("primary_job_title", "")), [proper(t) for t in split("other_titles")],
                                  split("skills"), history, education if "ed_count" in request.form else None,
                                  {key: request.form.get(key, "") for key in ("home_zip", "linkedin_url", "portfolio_url")}
                                  if "home_zip" in request.form else None)
    except jobfinder_db.JobFinderUnavailable as error:
        flash(str(error), "error")
    else:
        flash("Saved to your Job Finder profile.", "ok")
    return redirect(url_for("index") + "#profile")


@app.post("/upload")
def upload():
    file = request.files.get("resume")
    if not file or not file.filename:
        flash("Choose a PDF or Word file first.", "error")
        return redirect(url_for("index"))
    try:
        info = resume_file.save_upload(file.filename, file.read())
    except resume_file.ResumeFileError as error:
        flash(str(error), "error")
    else:
        flash(f"Uploaded {info['original_name']}.", "ok")
        try:
            added = profile_import.fill_profile(resume_file.current_text())
        except jobfinder_db.JobFinderUnavailable as error:
            flash(f"Your Profile wasn't filled in: {error}", "error")
        else:
            if added:
                flash("Filled in your Profile from the résumé: " + ", ".join(added) + ". Check it below.", "ok")
    return redirect(url_for("index"))


@app.get("/resume/page/<int:number>.png")
def resume_page(number):
    images = resume_file.current_page_images()
    if not 0 <= number < len(images):
        abort(404)
    return send_file(BytesIO(images[number]), mimetype="image/png", max_age=0)


@app.get("/files/<path:filename>")
def download(filename):
    try:
        builds.pdf_path(filename)
    except ValueError:
        abort(404)
    return send_from_directory(config.OUTPUT_DIR, filename, as_attachment=request.args.get("download") == "1",
                               max_age=0)


def _lines(name):
    return [line.strip() for line in request.form.get(name, "").splitlines() if line.strip()]


def _resume_from_form():
    sections = []
    for i in range(int(request.form.get("section_count", 0))):
        items = []
        for j in range(int(request.form.get(f"s{i}_count", 0))):
            key = f"s{i}_i{j}_"
            item = {field: request.form.get(key + field, "").strip()
                    for field in ("heading", "subheading", "location", "dates", "text")}
            item["bullets"] = _lines(key + "bullets")
            if any(item.values()):
                items.append(item)
        title, text = request.form.get(f"s{i}_title", "").strip(), request.form.get(f"s{i}_text", "").strip()
        if title or text or items:
            sections.append({"title": title, "text": text, "items": items})
    fields = [f for f in PrintedReference.model_fields]
    refs = [{f: request.form.get(f"ref{i}_{f}", "").strip() for f in fields}
            for i in range(min(int(request.form.get("ref_count", 0)), 50))]
    return {"full_name": request.form.get("full_name", "").strip(), "headline": request.form.get("headline", "").strip(),
            "contact": _lines("contact"), "sections": sections, "references": [r for r in refs if r["name"]]}


def _letter_from_form():
    body = request.form.get("body", "").replace("\r\n", "\n")
    return {"full_name": request.form.get("full_name", "").strip(), "contact": _lines("contact"),
            "date": request.form.get("date", "").strip(), "recipient": _lines("recipient"),
            "greeting": request.form.get("greeting", "").strip(),
            "paragraphs": [p.strip() for p in body.split("\n\n") if p.strip()],
            "closing": request.form.get("closing", "").strip(), "signature": request.form.get("signature", "").strip()}


def _layout_from_form():
    layout = {key: request.form.get(key) or None for key in DEFAULT_LAYOUT}
    for key in ("body_size", "name_size", "heading_size", "margin_in"):
        layout[key] = float(layout[key]) if layout[key] else None
    layout["heading_rule"] = request.form.get("heading_rule") == "on"
    return layout


@app.route("/build/<path:filename>", methods=["GET", "POST"])
def edit_build(filename):
    try:
        draft = builds.load_draft(filename)
    except (ValueError, FileNotFoundError):
        abort(404)
    if request.method == "POST":
        content = _resume_from_form() if draft["kind"] == "resume" else _letter_from_form()
        try:
            builds.save_build(draft["kind"], content, job_title=draft.get("job_title", ""),
                              company=draft.get("company", ""), job_id=draft.get("job_id"),
                              layout={**draft["layout"], **{k: v for k, v in _layout_from_form().items() if v is not None}},
                              replace=filename)
        except ValueError as error:
            flash(f"Not saved: {error}", "error")
        else:
            flash("Saved and redrawn.", "ok")
        return redirect(url_for("edit_build", filename=filename))
    fonts = available_fonts()
    fonts += [f for f in sorted(design.GOOGLE_FONTS) if f not in fonts]
    if draft["layout"]["font_family"] not in fonts:
        fonts.insert(0, draft["layout"]["font_family"])
    saved = [{k: v for k, v in r.items() if k in PrintedReference.model_fields} for r in references.load()]
    return render_template("build.html", draft=draft, fonts=fonts, saved_references=saved)


@app.post("/build/<path:filename>/delete")
def delete_build(filename):
    try:
        draft = builds.load_draft(filename)
    except (ValueError, FileNotFoundError):
        abort(404)
    builds.delete_build(filename)
    flash(f"Deleted {draft['pdf']}.", "ok")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=config.PORT, debug=False)

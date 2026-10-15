"""Skills to suggest while the Profile is edited: the ones the uploaded résumé mentions, the ones that go with the
primary job title, and the ones that go with skills already listed (Job Finder's own lists, as on its Dashboard)."""
import sys

import config
import profile_import
import resume_file


def suggest(skills, title, limit=16):
    if str(config.JOB_FINDER_DIR) not in sys.path:
        sys.path.append(str(config.JOB_FINDER_DIR))
    have = {s.casefold() for s in skills}
    ordered = []
    try:
        if resume_file.current_info():
            ordered += profile_import._detect_skills(resume_file.current_text())
    except Exception:  # an unreadable résumé just means fewer suggestions
        pass
    try:
        from jobfinder.web.profile_store import profile_skill_suggestions, related_skills_for
        ordered += profile_skill_suggestions({"primary_job_title": title, "skills": list(skills)})[:12]
        for skill in list(skills)[:6]:
            ordered += related_skills_for(skill)
    except Exception:  # Job Finder's lists unavailable: none is fine
        pass
    result = []
    for skill in ordered:
        if skill.casefold() not in have and skill.casefold() not in {r.casefold() for r in result}:
            result.append(skill)
    return result[:limit]

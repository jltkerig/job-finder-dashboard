"""What a person was to the user (the dropdown on a reference), and making a Supervisor the supervisor on one of their jobs."""
from models import RELATIONSHIPS


def job_label(job):
    """'Role at Company', the way a job is named in a reference's "your job together"."""
    return " at ".join(p for p in (job.get("role", ""), job.get("company", "")) if p)


def relationship_options(current=""):
    """The list to choose from. A value saved earlier that isn't on the list stays on it, so it isn't lost."""
    return RELATIONSHIPS + ([current] if current and current not in RELATIONSHIPS else [])


def job_choice(form, profile):
    """The index of the job picked in the form's "when you were" dropdown, or -1 if none (or none that exists)."""
    try:
        index = int(form.get("job", ""))
    except ValueError:
        return -1
    return index if 0 <= index < len((profile or {}).get("work_history") or []) else -1


def with_supervisor(work_history, index, person):
    """A copy of the work history with this person as the supervisor on one job. Keeps what the job already has
    where the person has nothing to give. `person` has name, and optionally title, email and phone."""
    history = [dict(w) for w in work_history]
    job = history[index]
    job.update({"supervisor_name": person["name"],
                "supervisor_title": person.get("title") or job.get("supervisor_title", ""),
                "supervisor_email": person.get("email") or job.get("supervisor_email", ""),
                "supervisor_phone": person.get("phone") or job.get("supervisor_phone", "")})
    return history

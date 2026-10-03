"""Which module a name must be patched in: the one that defines it (functions) or reads it (settings and state).

The search code is split over jobfinder/search/*.py. A test that replaces a function has to replace it in the module that
defines it, because every other module calls it through that module (docker.check_searxng_timer(), fetching.safe_request()).
"""
import importlib
import pkgutil

import job_finder
import jobfinder.search as search_package
from jobfinder.search import shared

MODULES = [job_finder, shared] + [importlib.import_module(f"jobfinder.search.{info.name}")
                                  for info in pkgutil.iter_modules(search_package.__path__) if info.name != "shared"]


def owner(name, default=job_finder):
    for module in MODULES:  # a function belongs to the module that defines it
        obj = module.__dict__.get(name)
        if callable(obj) and getattr(obj, "__module__", None) == module.__name__:
            return module
    for module in MODULES:  # a setting or table, to the first module that has it
        if name in module.__dict__:
            return module
    return default


def holders(name):
    """Every module that has this name (the definition and the modules that imported it), so a patch reaches all callers."""
    found = [module for module in MODULES if name in module.__dict__]
    return found or [job_finder]


def search_source():
    """The text of job_finder.py and every module in jobfinder/search/, for tests that check how the search is wired."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    files = [root / "job_finder.py", *sorted((root / "jobfinder" / "search").glob("*.py"))]
    return "\n".join(f.read_text(encoding="utf-8") for f in files)


def web_source():
    """The text of dashboard.py and every module in jobfinder/web/, for tests that check how the pages are wired."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    files = [root / "dashboard.py", *sorted((root / "jobfinder" / "web").glob("*.py"))]
    return "\n".join(f.read_text(encoding="utf-8") for f in files)
